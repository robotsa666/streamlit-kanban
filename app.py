# app.py (v3)
# Kanban with drag-and-drop directly on the main board (no extra DnD row).
# Slim items; click-and-drag the card itself to move; actions via sidebar.
# Streamlit 1.33+, Pydantic v2, streamlit-sortables.

from __future__ import annotations

import json
import uuid
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


PRIO_EMOJI = {"High": "🟥", "Med": "🟧", "Low": "🟩"}


def item_label(tid: str, t: Task) -> str:
    # Prosty, jednowierszowy label do drag&drop
    parts = [PRIO_EMOJI[t.priority], t.title]
    if t.due:
        parts.append(f"⏰ {t.due.isoformat()}")
    if t.tags:
        parts.append(" ".join("#"+x for x in t.tags))
    if t.done:
        parts.append("✅")
    return "  ·  ".join(parts)


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
    up = st.file_uploader("Import JSON (zastąpi bieżącą tablicę)", type=["json"])
    if up:
        try:
            raw = json.loads(up.read().decode("utf-8"))
            board = Board(**raw)
            save_board(board)
            st.success("Zaimportowano tablicę.")
            st.rerun()
        except Exception as e:
            st.error(f"Błąd walidacji importu: {e}")


def show_add_task_dialog():
    @st.dialog("Dodaj zadanie", width="large")
    def _dlg():
        board = get_board()
        with st.form("add_task_form", clear_on_submit=False):
            c = st.columns(2)
            title = c[0].text_input("Tytuł*", placeholder="Nazwa zadania")
            priority = c[1].selectbox("Priorytet", ["Low", "Med", "High"], index=1)
            desc = st.text_area("Opis", placeholder="Krótki opis...")
            c2 = st.columns(2)
            due_enabled = c2[0].checkbox("Ustaw termin")
            due_val = c2[0].date_input("Termin", value=date.today(), disabled=not due_enabled)
            tags_txt = c2[1].text_input("Tagi (rozdziel przecinkami)", placeholder="ops, ui, backend")
            col_map = {c.name: c.id for c in board.columns}
            column_id = st.selectbox("Kolumna docelowa", options=list(col_map.keys()))
            submitted = st.form_submit_button("➕ Dodaj", use_container_width=True)
            if submitted:
                if not title.strip():
                    st.error("Tytuł jest wymagany.")
                    return
                tags = [t.strip() for t in tags_txt.split(",") if t.strip()]
                due = due_val if due_enabled else None
                task = Task(title=title.strip(), desc=desc.strip(), priority=priority, due=due, tags=tags)
                add_task(col_map[column_id], task)
                st.success("Dodano zadanie.")
                st.rerun()
    _dlg()


def show_edit_task_dialog(task_id: str):
    @st.dialog("Edytuj zadanie", width="large")
    def _dlg():
        board = get_board()
        t = board.tasks[task_id]
        with st.form("edit_task_form"):
            c = st.columns(2)
            title = c[0].text_input("Tytuł*", value=t.title)
            priority = c[1].selectbox("Priorytet", ["Low", "Med", "High"], index=["Low", "Med", "High"].index(t.priority))
            desc = st.text_area("Opis", value=t.desc)
            c2 = st.columns(2)
            due_enabled = c2[0].checkbox("Ustaw termin", value=t.due is not None)
            due_val = c2[0].date_input("Termin", value=(t.due or date.today()), disabled=not due_enabled)
            tags_txt = c2[1].text_input("Tagi (rozdziel przecinkami)", value=", ".join(t.tags))
            col_map = {c.name: c.id for c in board.columns}
            current_col_id = next((c.id for c in board.columns if task_id in c.task_ids), board.columns[0].id)
            col_names = list(col_map.keys())
            current_col_name = next(name for name, cid in col_map.items() if cid == current_col_id)
            new_col_name = st.selectbox("Kolumna", options=col_names, index=col_names.index(current_col_name))

            submitted = st.form_submit_button("💾 Zapisz", use_container_width=True)
            if submitted:
                if not title.strip():
                    st.error("Tytuł jest wymagany.")
                    return
                tags = [t.strip() for t in tags_txt.split(",") if t.strip()]
                due = due_val if due_enabled else None
                edit_task(task_id, {"title": title.strip(), "desc": desc.strip(), "priority": priority, "due": due, "tags": tags})
                new_col_id = col_map[new_col_name]
                if new_col_id != current_col_id:
                    board2 = get_board()
                    for c in board2.columns:
                        if task_id in c.task_ids:
                            c.task_ids.remove(task_id)
                    for c in board2.columns:
                        if c.id == new_col_id:
                            c.task_ids.append(task_id)
                    save_board(board2)
                st.success("Zapisano zmiany.")
                st.rerun()
    _dlg()


# ========== APP ==========

st.set_page_config(page_title="Kanban – Streamlit", page_icon="🗂️", layout="wide")

# Smuklejszy wygląd itemów i kolumn; zero HTML w itemach, tylko tekst/emoji.
st.markdown(
    """
    <style>
      .block-container { padding-top: .6rem; }
      .sortable-container { background: rgba(127,127,127,.06); border-radius: 10px; padding: 8px; }
      .sortable-container-header { font-weight: 700; margin: 0 0 6px 2px; }
      .sortable-item { background: var(--background-color); border: 1px solid rgba(127,127,127,.35);
                       border-radius: 8px; padding: 6px 10px; margin: 6px 0; font-size: .95rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

board = get_board()

# Sidebar: filtry + IO + kolumny + akcje zadaniowe
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

st.sidebar.divider()
if st.sidebar.button("➕ Dodaj zadanie", use_container_width=True):
    show_add_task_dialog()

# Akcje na zadaniach (wybór z listy), bo karty są „czyste” pod DnD
with st.sidebar.expander("🛠️ Edycja/Usuwanie zadania"):
    # Budujemy listę: "Kolumna: Tytuł" -> task_id
    task_choices = []
    for c in board.columns:
        for tid in c.task_ids:
            t = board.tasks.get(tid)
            if t:
                task_choices.append((f"{c.name}: {t.title}", tid))
    if task_choices:
        chosen_label = st.selectbox("Wybierz zadanie", options=[lbl for lbl, _ in task_choices])
        chosen_tid = dict(task_choices)[chosen_label]
        c1, c2, c3 = st.columns(3)
        if c1.button("✏️ Edytuj", use_container_width=True):
            st.session_state.edit_task_id = chosen_tid
            st.experimental_rerun()
        if c2.button("🗑️ Usuń", use_container_width=True):
            delete_task(chosen_tid)
            st.success("Usunięto zadanie.")
            st.experimental_rerun()
        cur_done = board.tasks[chosen_tid].done
        if c3.button("✅ Przełącz Done", use_container_width=True):
            edit_task(chosen_tid, {"done": not cur_done})
            st.experimental_rerun()

if st.session_state.get("edit_task_id"):
    show_edit_task_dialog(st.session_state.pop("edit_task_id"))

# ======= GŁÓWNA TABLICA – DRAG & DROP =======

st.subheader("📋 Tablica Kanban (przeciągaj karty między kolumnami)")

# Zastosuj filtry do wyświetlania (ale dnd operuje na pełnym zestawie – więc filtrujemy label)
def pass_filter(t: Task) -> bool:
    ok_title = title_filter.lower() in t.title.lower() if title_filter else True
    ok_prio = (t.priority in prio_filter) if prio_filter else True
    ok_tags = (not tags_filter) or (set(tags_filter) & set(t.tags))
    return ok_title and ok_prio and ok_tags

containers = []
for col in board.columns:
    items = []
    for tid in col.task_ids:
        t = board.tasks.get(tid)
        if not t:
            continue
        label = item_label(tid, t) if pass_filter(t) else f"⏸️ (ukryte filtrem) {t.title}"
        items.append(f"{tid}::{label}")
    containers.append({"header": f"{col.name}", "items": items})

result = sort_items(
    containers,
    multi_containers=True,
    direction="vertical",
    key="kanban-main",
)

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
        st.toast("Zaktualizowano układ zadań.")
        st.rerun()

with st.expander("ℹ️ Wskazówki"):
    st.markdown(
        """
- Karty są smukłe – kliknij i **przeciągnij** kartę, aby przenieść między kolumnami lub zmienić kolejność.  
- Edycja/Usuwanie/Done → w **sidebarze** w sekcji „🛠️ Edycja/Usuwanie zadania”.  
- Import zastępuje aktualny stan; Export pobiera kopię tablicy.
        """
    )
