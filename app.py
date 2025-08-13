# app.py (v2)
# Ulepszony layout + pewniejsze mapowanie DnD dla streamlit-sortables.
# - Wyraźny panel DnD u góry (toggle)
# - Karty z kompaktowym paskiem akcji
# - Solidna obsługa różnych formatów zwrotu komponentu
# - Naprawa styli w dark mode

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
        # dopnij osierocone do pierwszej kolumny
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


def label_for(tid: str, t: Task) -> str:
    due_txt = f" · ⏰ {t.due.isoformat()}" if t.due else ""
    done_txt = " ✅" if t.done else ""
    tag_txt = f" · #{', #'.join(t.tags)}" if t.tags else ""
    return f"{PRIO_EMOJI[t.priority]} {t.title}{due_txt}{tag_txt}{done_txt}"


def apply_filters(tasks: dict[str, Task], title_sub: str, priorities: list[Priority], tags: list[str]) -> set[str]:
    def match(t: Task) -> bool:
        ok_title = title_sub.lower() in t.title.lower() if title_sub else True
        ok_prio = (t.priority in priorities) if priorities else True
        ok_tags = (not tags) or (set(tags) & set(t.tags))
        return ok_title and ok_prio and ok_tags

    return {tid for tid, t in tasks.items() if match(t)}


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
            board = Board(**raw)  # walidacja
            save_board(board)
            st.success("Zaimportowano tablicę.")
            st.rerun()
        except Exception as e:
            st.error(f"Błąd walidacji importu: {e}")


def render_task_card(tid: str, t: Task):
    color = {"High": "#ef4444", "Med": "#f59e0b", "Low": "#10b981"}[t.priority]
    # karta
    st.markdown(
        f"""
        <div style="border-radius:10px;border:1px solid var(--secondary-background-color,#2b2b2b);
                    background: var(--background-color, #111); padding:10px; margin-bottom:10px;">
          <div style="display:flex; gap:10px;">
            <div style="width:5px; background:{color}; border-radius:4px;"></div>
            <div style="flex:1;">
              <div style="font-weight:700;">{t.title}</div>
              <div style="opacity:.9; font-size:.95rem;">{t.desc or ""}</div>
              <div style="font-size:.85rem; margin-top:4px; opacity:.9;">
                <b>Priorytet:</b> {t.priority}
                {' · ⏰ ' + t.due.isoformat() if t.due else ''}
                {' · ' + ' '.join(f'#'+x for x in t.tags) if t.tags else ''}
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    # pasek akcji (kompaktowy)
    c1, c2, c3, c4 = st.columns([1, 1, 2, 2])
    if c1.button("✏️", key=f"edit-{tid}", help="Edytuj"):
        st.session_state.edit_task_id = tid
        st.rerun()
    if c2.button("🗑️", key=f"del-{tid}", help="Usuń"):
        delete_task(tid)
        st.success("Usunięto zadanie.")
        st.rerun()
    new_done = c3.toggle("Done", value=t.done, key=f"done-{tid}")
    if new_done != t.done:
        edit_task(tid, {"done": new_done})
        st.rerun()
    new_prio = c4.selectbox("Priorytet", ["Low", "Med", "High"], index=["Low", "Med", "High"].index(t.priority), key=f"prio-{tid}")
    if new_prio != t.priority:
        edit_task(tid, {"priority": new_prio})
        st.rerun()


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


# ======== APP ========

st.set_page_config(page_title="Kanban – Streamlit", page_icon="🗂️", layout="wide")

# Naprawy stylu (dark mode + zwarta typografia)
st.markdown(
    """
    <style>
      .block-container { padding-top: 0.8rem; }
      .stDialog .stButton>button { width: 100%; }
      /* Sortables wygląd */
      .sortable-container { background: rgba(127,127,127,0.08); border-radius: 10px; padding: 10px; }
      .sortable-item { background: var(--background-color); border: 1px solid rgba(127,127,127,0.35);
                       border-radius: 8px; padding: 6px 10px; margin: 6px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

board = get_board()

# Sidebar: Filtry + IO + Kolumny
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

# DnD panel (toggle widoczności, domyślnie włączony)
with st.container():
    st.subheader("🔁 Ułóż zadania (Drag & Drop)")
    show_dnd = st.toggle("Pokaż obszar przeciągania", value=True)
    if show_dnd:
        # przygotuj strukturę wejściową
        sortable_input = []
        for col in board.columns:
            items = [f"{tid}::{label_for(tid, board.tasks[tid])}" for tid in col.task_ids if tid in board.tasks]
            sortable_input.append({"header": col.name, "items": items})

        # DnD
        sorted_items = sort_items(
            sortable_input,
            multi_containers=True,
            direction="vertical",
            key="kanban-sortables",
        )

        # Normalizacja struktury zwrotnej (różne wersje komponentu)
        def _extract_list(container_result):
            if isinstance(container_result, dict) and "items" in container_result:
                return container_result["items"]
            if isinstance(container_result, list):
                return container_result
            # fallback na inne klucze, jeśli kiedyś się pojawią
            for k in ("order", "values"):
                if isinstance(container_result, dict) and k in container_result:
                    return container_result[k]
            return []

        if sorted_items is not None:
            normalized = [_extract_list(c) for c in sorted_items]
            # przemapuj na task_ids
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

st.divider()

# Widok kanban (filtry) – responsywny: 1–3 kolumny
st.subheader("📋 Tablica Kanban")
match_ids = apply_filters(board.tasks, title_filter, prio_filter, tags_filter)

num_cols = 3 if len(board.columns) >= 3 else len(board.columns) or 1
cols = st.columns(num_cols)
for i, col in enumerate(board.columns):
    with cols[i % num_cols]:
        st.markdown(f"### {col.name}")
        st.caption(f"{len(col.task_ids)} zadań")
        for tid in col.task_ids:
            if tid in match_ids:
                render_task_card(tid, board.tasks[tid])
        st.write("")  # spacing

with st.expander("ℹ️ Wskazówki"):
    st.markdown(
        """
- Drag & drop działa w górnym panelu „Ułóż zadania (Drag & Drop)”.  
- Filtry wpływają na widok kart, nie na panel DnD (dla spójności układu).  
- Import JSON zastępuje bieżący stan; Export JSON pobiera kopię tablicy.
        """
    )
