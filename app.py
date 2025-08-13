# app.py (v3.4)
# - Tekst kart bez emoji/symboli + wymuszenie koloru czcionki (ciemne motywy)
# - Klucz DnD oparty o hash tablicy (natychmiastowy refresh)
# - Diagnostyka: surowe listy items pod tablicą
# - Stabilny formularz w sidebarze + import resetem

from __future__ import annotations

import json
import uuid
import hashlib
from datetime import date
from typing import Literal, Optional

import streamlit as st
from pydantic import BaseModel, Field, field_validator, model_validator
from streamlit_sortables import sort_items

Priority = Literal["Low", "Med", "High"]


class Task(BaseModel):
    title: str = Field(min_length=1)
    desc: str = ""
    priority: Priority = "Med"
    due: Optional[date] = None
    tags: list[str] = Field(default_factory=list)
    done: bool = False

    @field_validator("due", mode="before")
    @classmethod
    def parse_due(cls, v):
        if v in ("", None):
            return None
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v


class ColumnModel(BaseModel):
    id: str
    name: str
    task_ids: list[str] = Field(default_factory=list)


class Board(BaseModel):
    columns: list[ColumnModel]
    tasks: dict[str, Task]

    @model_validator(mode="after")
    def check_references(self):
        task_keys = set(self.tasks.keys())
        seen = set()
        for col in self.columns:
            if col.id in seen:
                raise ValueError(f"Duplicate column id '{col.id}'.")
            seen.add(col.id)
            for tid in col.task_ids:
                if tid not in task_keys:
                    raise ValueError(f"Task id '{tid}' in column '{col.name}' not found.")
        assigned = {tid for c in self.columns for tid in c.task_ids}
        orphans = set(self.tasks.keys()) - assigned
        if orphans and self.columns:
            self.columns[0].task_ids.extend(sorted(list(orphans)))
        return self


DEFAULT_BOARD = Board(
    columns=[
        ColumnModel(id="todo", name="Do zrobienia", task_ids=[]),
        ColumnModel(id="inprog", name="W trakcie", task_ids=[]),
        ColumnModel(id="done", name="Zrobione", task_ids=[]),
    ],
    tasks={},
)


def get_board() -> Board:
    if "board" not in st.session_state:
        st.session_state.board = DEFAULT_BOARD.model_dump(mode="json")
    return Board(**st.session_state.board)


def save_board(board: Board):
    st.session_state.board = board.model_dump(mode="json")


def next_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def add_task(column_id: str, t: Task) -> str:
    board = get_board()
    tid = next_id("t")
    board.tasks[tid] = t
    for col in board.columns:
        if col.id == column_id:
            col.task_ids.append(tid)
            break
    save_board(board)
    return tid


def edit_task(task_id: str, updates: dict):
    board = get_board()
    if task_id not in board.tasks:
        st.error("Nie znaleziono zadania.")
        return
    current = board.tasks[task_id]
    new = current.model_copy(update=updates)
    board.tasks[task_id] = Task(**new.model_dump())
    save_board(board)


def delete_task(task_id: str):
    board = get_board()
    board.tasks.pop(task_id, None)
    for col in board.columns:
        if task_id in col.task_ids:
            col.task_ids.remove(task_id)
    save_board(board)


def add_column(name: str) -> str:
    board = get_board()
    cid = next_id("c")
    board.columns.append(ColumnModel(id=cid, name=name))
    save_board(board)
    return cid


def rename_column(column_id: str, new_name: str):
    board = get_board()
    for col in board.columns:
        if col.id == column_id:
            col.name = new_name
            break
    save_board(board)


def delete_column(column_id: str, move_tasks_to: Optional[str] = None):
    board = get_board()
    idx = next((i for i, c in enumerate(board.columns) if c.id == column_id), None)
    if idx is None:
        st.error("Kolumna nie istnieje.")
        return
    col = board.columns[idx]
    if col.task_ids and not move_tasks_to:
        st.error("Kolumna nie jest pusta. Wybierz kolumnę docelową do przeniesienia zadań.")
        return
    if move_tasks_to:
        for c in board.columns:
            if c.id == move_tasks_to:
                c.task_ids.extend(col.task_ids)
                break
    del board.columns[idx]
    save_board(board)


def item_label(t: Task) -> str:
    # Prostolinijna, „bezpieczna” etykieta
    parts = [t.title]
    if t.priority:
        parts.append(f"[{t.priority}]")
    if t.due:
        parts.append(t.due.isoformat())
    if t.tags:
        parts.append(" ".join("#"+x for x in t.tags))
    if t.done:
        parts.append("(Done)")
    return " · ".join(parts)


def export_json_button(board: Board):
    data = board.model_dump(mode="json")
    for tid, t in data["tasks"].items():
        if t.get("due") is None:
            t["due"] = ""
    st.download_button(
        label="⬇️ Export JSON",
        file_name="board.json",
        mime="application/json",
        data=json.dumps(data, ensure_ascii=False, indent=2),
        use_container_width=True,
    )


def import_json_uploader():
    token = st.session_state.get("_import_token", "0")
    up = st.file_uploader("Import JSON (zastąpi bieżącą tablicę)", type=["json"], key=f"import_{token}")
    if up is not None:
        try:
            raw = json.loads(up.read().decode("utf-8"))
            board = Board(**raw)
            save_board(board)
            st.success("Zaimportowano tablicę.")
            st.session_state["_import_token"] = next_id("tok")
            st.rerun()
        except Exception as e:
            st.error(f"Błąd walidacji importu: {e}")


# ===== APP =====

st.set_page_config(page_title="Kanban – Streamlit", page_icon="🗂️", layout="wide")

st.markdown(
    """
    <style>
      .block-container { padding-top: .6rem; }
      .sortable-container { background: rgba(127,127,127,.06); border-radius: 10px; padding: 8px; }
      .sortable-container-header { font-weight: 700; margin: 0 0 6px 2px; }
      .sortable-item { background: var(--background-color); border: 1px solid rgba(127,127,127,.35);
                       border-radius: 8px; padding: 6px 10px; margin: 6px 0; font-size: .95rem;
                       color: var(--text-color, #fff); } /* Wymuś widoczny kolor tekstu */
    </style>
    """,
    unsafe_allow_html=True,
)

board = get_board()

# Sidebar: Filtry + IO + Kolumny + Dodawanie
st.sidebar.header("🔎 Filtry")
title_filter = st.sidebar.text_input("Tytuł zawiera…", placeholder="np. raport")
prio_filter = st.sidebar.multiselect("Priorytet", options=["Low", "Med", "High"])
all_tags = sorted({tag for t in board.tasks.values() for tag in t.tags})
tags_filter = st.sidebar.multiselect("Tagi", options=all_tags)

st.sidebar.divider()
st.sidebar.header("💾 Import / Export")
export_json_button(board)
import_json_uploader()

st.sidebar.divider()
st.sidebar.header("🧱 Kolumny")
with st.sidebar.expander("Dodaj kolumnę"):
    new_col_name = st.text_input("Nazwa nowej kolumny", key="new_col_name")
    if st.button("➕ Dodaj kolumnę", use_container_width=True):
        if new_col_name.strip():
            add_column(new_col_name.strip())
            st.success("Dodano kolumnę.")
            st.rerun()
        else:
            st.error("Podaj nazwę kolumny.")

with st.sidebar.expander("Zmień nazwę kolumny"):
    col_opts = {c.name: c.id for c in board.columns}
    if col_opts:
        sel_name = st.selectbox("Kolumna", options=list(col_opts.keys()), key="rename_sel")
        new_name = st.text_input("Nowa nazwa", key="rename_val")
        if st.button("✏️ Zmień nazwę", use_container_width=True):
            if new_name.strip():
                rename_column(col_opts[sel_name], new_name.strip())
                st.success("Zmieniono nazwę kolumny.")
                st.rerun()
            else:
                st.error("Podaj nową nazwę.")

with st.sidebar.expander("Usuń kolumnę"):
    col_opts2 = {c.name: c.id for c in board.columns}
    if col_opts2:
        del_name = st.selectbox("Kolumna do usunięcia", options=list(col_opts2.keys()), key="del_col_sel")
        others = [(c.name, c.id) for c in board.columns if c.name != del_name]
        tgt_name = st.selectbox("Przenieś zadania do…", options=["—"] + [n for n, _ in others], key="move_to_sel")
        confirm = st.checkbox("Potwierdzam usunięcie")
        if st.button("🗑️ Usuń kolumnę", use_container_width=True, disabled=not confirm):
            move_to = None
            if tgt_name != "—":
                move_to = dict(others)[tgt_name]
            delete_column(col_opts2[del_name], move_to)
            st.rerun()

# Dodawanie zadań
st.sidebar.divider()
st.sidebar.header("➕ Dodaj zadanie")
with st.sidebar.form("add_task_form_sidebar", clear_on_submit=True):
    c = st.columns(2)
    add_title = c[0].text_input("Tytuł*", placeholder="Nazwa zadania", key="sb_add_title")
    add_prio = c[1].selectbox("Priorytet", ["Low", "Med", "High"], index=1, key="sb_add_priority")
    add_desc = st.text_area("Opis", placeholder="Krótki opis...", key="sb_add_desc")
    c2 = st.columns(2)
    add_due_enabled = c2[0].checkbox("Ustaw termin", key="sb_add_due_enabled")
    add_due_val = c2[0].date_input("Termin", value=date.today(), disabled=not add_due_enabled, key="sb_add_due_val")
    add_tags_txt = c2[1].text_input("Tagi (rozdziel przecinkami)", placeholder="ops, ui, backend", key="sb_add_tags")
    col_map = {c.name: c.id for c in board.columns}
    add_colname = st.selectbox("Kolumna docelowa", options=list(col_map.keys()) if col_map else [], key="sb_add_col")
    sb_submitted = st.form_submit_button("Dodaj", use_container_width=True)
    if sb_submitted:
        if not add_title or not add_title.strip():
            st.error("Tytuł jest wymagany.")
        elif not col_map:
            st.error("Brak kolumn. Dodaj najpierw kolumnę w sekcji 'Kolumny'.")
        else:
            tags = [t.strip() for t in add_tags_txt.split(",") if t.strip()]
            due = add_due_val if add_due_enabled else None
            task = Task(title=add_title.strip(), desc=(add_desc or "").strip(), priority=add_prio, due=due, tags=tags)
            add_task(col_map[add_colname], task)
            st.session_state["_force_refresh"] = uuid.uuid4().hex
            st.success("Dodano zadanie.")
            st.rerun()

# ===== Board DnD (klucz = hash) =====

st.subheader("📋 Tablica Kanban")

def pass_filter(t: Task) -> bool:
    ok_title = title_filter.lower() in t.title.lower() if title_filter else True
    ok_prio = (t.priority in prio_filter) if prio_filter else True
    ok_tags = (not tags_filter) or (set(tags_filter) & set(t.tags))
    return ok_title and ok_prio and ok_tags

containers = []
raw_debug = {}
for col in board.columns:
    items = []
    for tid in col.task_ids:
        t = board.tasks.get(tid)
        if not t:
            continue
        label = item_label(t) if pass_filter(t) else f"(ukryte filtrem) {t.title}"
        items.append(f"{tid}::{label}")
    containers.append({"header": f"{col.name} ({len(items)})", "items": items})
    raw_debug[col.name] = items

board_json = json.dumps(board.model_dump(mode="json"), sort_keys=True)
board_hash = hashlib.md5(board_json.encode("utf-8")).hexdigest()[:8]
extra_rev = st.session_state.get("_force_refresh", "")
comp_key = f"kanban-main-{board_hash}-{extra_rev}"

result = sort_items(containers, multi_containers=True, direction="vertical", key=comp_key)

def _extract_items(container_result):
    if container_result is None:
        return []
    if isinstance(container_result, dict) and "items" in container_result:
        return container_result["items"]
    if isinstance(container_result, list):
        return container_result
    for k in ("order", "values"):
        if isinstance(container_result, dict) and k in container_result:
            return container_result[k]
    return []

if result is not None:
    normalized = [_extract_items(c) for c in result]
    changed = False
    board2 = get_board()
    for i, col in enumerate(board2.columns):
        new_ids = [s.split("::", 1)[0] for s in (normalized[i] if i < len(normalized) else [])]
        if new_ids != col.task_ids:
            col.task_ids = new_ids
            changed = True
    if changed:
        save_board(board2)
        st.rerun()

with st.expander("🛠️ Diagnostyka (surowe items)"):
    st.json(raw_debug)
