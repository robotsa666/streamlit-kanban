# app.py — Kanban (Projekty + React UI + smooth DnD + Supabase + Modale Add/Edit)
# Wersja: v5.4.2-projects-inline-modal
# - FIX: zamiast pełnoekranowego overlay, bezpieczny fallback „inline panel” (nie blokuje strony)
# - Natywne st.modal jeśli dostępne; w przeciwnym razie panel w treści
# - Projekty (lista + wyszukiwarka + CRUD) w sidebarze
# - Unikalne key= dla widżetów (brak konfliktów)
# - DnD: streamlit-sortables
# - Karty: 4 linie (tytuł, opis, data, Priorytet: X)
# - Persistencja: Supabase (1 wiersz = 1 projekt) lub fallback w session_state

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Literal, Optional

import streamlit as st
from pydantic import BaseModel, Field, field_validator, model_validator
from streamlit_sortables import sort_items
from streamlit_elements import elements, mui

BUILD_TAG = "v5.4.2-projects-inline-modal"
REV_KEY = "_view_rev"

# ───────────────────────── Polyfill modala (INLINE) ───────────────────────── #
def _modal(title: str, key: str | None = None):
    """
    • Jeśli jest st.modal (nowy Streamlit) → użyj go.
    • W przeciwnym razie zwróć panel „inline” w treści strony (nie blokuje kliknięć).
    """
    if getattr(st, "modal", None):
        try:
            return st.modal(title, key=key)
        except TypeError:
            return st.modal(title)

    class _InlinePanel:
        def __enter__(self):
            st.markdown(
                """
                <div style="
                    margin: 12px auto 8px auto;
                    max-width: 760px;
                    padding: 16px 16px 2px 16px;
                    border-radius: 12px;
                    background: rgba(28,28,30,.95);
                    border: 1px solid rgba(255,255,255,.08);
                    box-shadow: 0 10px 30px rgba(0,0,0,.35);
                ">
                """,
                unsafe_allow_html=True,
            )
            st.markdown(f"#### {title}")
            return st.container()
        def __exit__(self, exc_type, exc, tb):
            st.markdown("</div>", unsafe_allow_html=True)
            return False
    return _InlinePanel()

# ───────────────────────── Supabase helpers ───────────────────────── #
def _sb_table_name() -> str:
    return st.secrets.get("SUPABASE_TABLE", "boards")

def _sb_client():
    try:
        from supabase import create_client
    except Exception:
        return None
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception as e:
        st.error(f"Nie udało się połączyć z Supabase: {e}")
        return None

# ───────────────────────── Modele danych ───────────────────────── #
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
                    raise ValueError(f"Task id '{tid}' w kolumnie '{col.name}' nie istnieje.")
        assigned = {tid for c in self.columns for tid in c.task_ids}
        orphans = set(self.tasks.keys()) - assigned
        if orphans and self.columns:
            self.columns[0].task_ids.extend(sorted(list(orphans)))
        return self

DEFAULT_BOARD = Board(
    columns=[
        ColumnModel(id="todo",   name="Do zrobienia", task_ids=[]),
        ColumnModel(id="inprog", name="W trakcie",   task_ids=[]),
        ColumnModel(id="done",   name="Zrobione",    task_ids=[]),
    ],
    tasks={},
)

# ───────────────────────── Projekty: stan + DB ───────────────────────── #
def _default_project_id() -> str:
    return st.session_state.get("project_id") or "main"

def _set_project_id(pid: str):
    st.session_state["project_id"] = pid

def _ss_projects_store() -> dict:
    if "projects_store" not in st.session_state:
        st.session_state["projects_store"] = {}
    return st.session_state["projects_store"]

def db_project_exists(pid: str) -> bool:
    sb = _sb_client()
    if not sb:
        return pid in _ss_projects_store()
    try:
        resp = sb.table(_sb_table_name()).select("id").eq("id", pid).limit(1).execute()
        return bool(resp.data)
    except Exception:
        return False

def db_list_projects() -> list[str]:
    sb = _sb_client()
    if not sb:
        store = _ss_projects_store()
        if not store:
            store["main"] = DEFAULT_BOARD.model_dump(mode="json")
        return sorted(store.keys(), key=str.lower)
    try:
        resp = sb.table(_sb_table_name()).select("id").order("id", desc=False).execute()
        ids = [r["id"] for r in (resp.data or [])]
        if not ids:
            sb.table(_sb_table_name()).upsert(
                {"id": "main", "data": DEFAULT_BOARD.model_dump(mode="json"), "updated_at": datetime.utcnow().isoformat()}
            ).execute()
            return ["main"]
        return ids
    except Exception as e:
        st.error(f"DB list projects error: {e}")
        return ["main"]

def db_load_board_default_insert(pid: str, default_payload: dict) -> dict | None:
    sb = _sb_client()
    if not sb:
        store = _ss_projects_store()
        if pid not in store:
            store[pid] = default_payload
        return store[pid]
    try:
        resp = sb.table(_sb_table_name()).select("data").eq("id", pid).limit(1).execute()
        rows = resp.data or []
        if not rows:
            sb.table(_sb_table_name()).upsert(
                {"id": pid, "data": default_payload, "updated_at": datetime.utcnow().isoformat()}
            ).execute()
            return default_payload
        return rows[0]["data"]
    except Exception as e:
        st.error(f"DB load error: {e}")
        return None

def db_load_board_raw(pid: str):
    sb = _sb_client()
    if not sb:
        store = _ss_projects_store()
        data = store.get(pid)
        if data is None:
            return None, "not_found"
        return {"id": pid, "data": data, "updated_at": "session"}, "ok"
    try:
        resp = sb.table(_sb_table_name()).select("*").eq("id", pid).limit(1).execute()
        rows = resp.data or []
        if not rows:
            return None, "not_found"
        return rows[0], "ok"
    except Exception as e:
        return {"error": str(e)}, "error"

def db_save_board(pid: str, board_dict: dict) -> None:
    sb = _sb_client()
    if not sb:
        _ss_projects_store()[pid] = board_dict
        st.session_state["_last_db_save"] = datetime.utcnow().strftime("%H:%M:%S")
        return
    try:
        payload = {"id": pid, "data": board_dict, "updated_at": datetime.utcnow().isoformat()}
        sb.table(_sb_table_name()).upsert(payload).execute()
        st.session_state["_last_db_save"] = datetime.utcnow().strftime("%H:%M:%S")
    except Exception as e:
        st.error(f"DB save error: {e}")

def db_delete_project(pid: str) -> None:
    sb = _sb_client()
    if not sb:
        _ss_projects_store().pop(pid, None)
        return
    try:
        sb.table(_sb_table_name()).delete().eq("id", pid).execute()
    except Exception as e:
        st.error(f"DB delete project error: {e}")

def db_clone_project(old_id: str, new_id: str) -> bool:
    data = db_load_board_default_insert(old_id, DEFAULT_BOARD.model_dump(mode="json"))
    if data is None:
        return False
    if db_project_exists(new_id):
        st.error("Projekt o takiej nazwie już istnieje.")
        return False
    db_save_board(new_id, data)
    db_delete_project(old_id)
    return True

# ───────────────────────── Board API (per projekt) ───────────────────────── #
def current_project() -> str:
    pid = st.session_state.get("project_id")
    if not pid:
        pid = _default_project_id()
        _set_project_id(pid)
    return pid

def get_board() -> Board:
    pid = current_project()
    if "board" not in st.session_state or st.session_state.get("_board_pid") != pid:
        data = db_load_board_default_insert(pid, DEFAULT_BOARD.model_dump(mode="json"))
        st.session_state.board = data if data else DEFAULT_BOARD.model_dump(mode="json")
        st.session_state["_board_pid"] = pid
    return Board(**st.session_state.board)

def save_board(board: Board):
    pid = current_project()
    as_dict = board.model_dump(mode="json")
    st.session_state.board = as_dict
    st.session_state["_board_pid"] = pid
    db_save_board(pid, as_dict)

def bump_rev():
    st.session_state[REV_KEY] = st.session_state.get(REV_KEY, 0) + 1

def switch_project(pid: str):
    _set_project_id(pid)
    st.session_state.pop("board", None)
    st.session_state["_board_pid"] = pid
    bump_rev()
    st.rerun()

# ───────────────────────── Operacje na zadaniach/kolumnach ───────────────────────── #
def next_id(prefix: str) -> str:
    import uuid as _uuid
    return f"{prefix}-{_uuid.uuid4().hex[:8]}"

def add_task(column_id: str, t: Task) -> str:
    b = get_board()
    tid = next_id("t")
    b.tasks[tid] = t
    for c in b.columns:
        if c.id == column_id:
            c.task_ids.append(tid)
            break
    save_board(b); bump_rev(); return tid

def edit_task(task_id: str, updates: dict):
    b = get_board()
    if task_id not in b.tasks:
        st.error("Nie znaleziono zadania.")
        return
    new = b.tasks[task_id].model_copy(update=updates)
    b.tasks[task_id] = Task(**new.model_dump())
    save_board(b)

def delete_task(task_id: str):
    b = get_board()
    b.tasks.pop(task_id, None)
    for c in b.columns:
        if task_id in c.task_ids:
            c.task_ids.remove(task_id)
    save_board(b)

def add_column(name: str) -> str:
    b = get_board()
    cid = next_id("c")
    b.columns.append(ColumnModel(id=cid, name=name))
    save_board(b); bump_rev(); return cid

def rename_column(column_id: str, new_name: str):
    b = get_board()
    for c in b.columns:
        if c.id == column_id:
            c.name = new_name
            break
    save_board(b); bump_rev()

def delete_column(column_id: str, move_tasks_to: Optional[str] = None):
    b = get_board()
    idx = next((i for i, c in enumerate(b.columns) if c.id == column_id), None)
    if idx is None:
        st.error("Kolumna nie istnieje.")
        return
    col = b.columns[idx]
    if col.task_ids and not move_tasks_to:
        st.error("Kolumna nie jest pusta. Wybierz kolumnę docelową.")
        return
    if move_tasks_to:
        for c in b.columns:
            if c.id == move_tasks_to:
                c.task_ids.extend(col.task_ids)
                break
    del b.columns[idx]
    save_board(b); bump_rev()

# ───────────────────────── Etykieta + ukryte ID ───────────────────────── #
def item_label_multiline(t: Task) -> str:
    title = (t.title or "").strip()
    desc  = (t.desc  or "").strip()
    due   = t.due.isoformat() if t.due else ""
    prio  = f"Priorytet: {t.priority}"
    return "\n".join([title, desc, due, prio])

_HIDDEN = "\u2063"
def encode_item(label: str, tid: str) -> str: return f"{label}{_HIDDEN}{tid}"
def decode_item_id(s: str) -> str:
    if _HIDDEN in s: return s.rsplit(_HIDDEN, 1)[-1]
    if "::" in s:    return s.split("::", 1)[0]
    return s

# ───────────────────────── Import / Export ───────────────────────── #
def export_json_button(board: Board, pid: str):
    data = board.model_dump(mode="json")
    for _, t in data["tasks"].items():
        if t.get("due") is None: t["due"] = ""
    st.download_button("⬇️ Export JSON",
                       json.dumps(data, ensure_ascii=False, indent=2),
                       file_name=f"{pid}_board.json",
                       mime="application/json",
                       use_container_width=True,
                       key="export_json_btn")

def import_json_uploader(pid: str):
    token = st.session_state.get("_import_token", "0")
    up = st.file_uploader(f"Import JSON do projektu “{pid}” (zastąpi bieżącą tablicę)",
                          type=["json"],
                          key=f"import_{token}")
    if up is not None:
        try:
            raw = json.loads(up.read().decode("utf-8")); board = Board(**raw)
            save_board(board); bump_rev(); st.success("Zaimportowano tablicę."); st.rerun()
        except Exception as e:
            st.error(f"Błąd walidacji importu: {e}")

# ───────────────────────── UI / Styl ───────────────────────── #
st.set_page_config(page_title="Kanban – Projekty", page_icon="🗂️", layout="wide")
st.markdown("""
<style>
  .sortable-container { background: rgba(127,127,127,.08); border-radius: 10px; padding: 10px; min-height: 64px; }
  .sortable-item { background: var(--background-color); border: 1px solid rgba(127,127,127,.35);
                   border-radius: 8px; padding: 8px 10px; margin: 6px 0; font-size: .95rem;
                   color: var(--text-color, #fff);
                   white-space: pre-line; line-height: 1.25;
                   transition: transform .08s ease, background-color .08s ease, box-shadow .08s ease; }
  .sortable-item::first-line { font-weight: 700; }
  .block-container { padding-top: .6rem; }
  .stButton>button { margin-bottom: 0 !important; }
</style>
""", unsafe_allow_html=True)

# ───────────────────────── SIDEBAR: Projekty ───────────────────────── #
with st.sidebar:
    st.info(f"Build: {BUILD_TAG}")

all_projects = db_list_projects()
if "project_id" not in st.session_state:
    _set_project_id(all_projects[0] if all_projects else "main")

with st.sidebar.expander("📁 Projekt – wybór", expanded=True):
    q = st.text_input("Szukaj projektu…", placeholder="pisz, aby filtrować", key="project_search")
    filtered = [p for p in all_projects if (q.lower() in p.lower())] if q else all_projects
    cur_pid = current_project()
    if cur_pid not in filtered and cur_pid in all_projects:
        filtered = [cur_pid] + filtered
    if not filtered:
        filtered = all_projects or ["main"]
    sel = st.selectbox("Wybierz projekt", options=filtered,
                       index=filtered.index(cur_pid) if cur_pid in filtered else 0,
                       key="project_select")
    if sel != cur_pid:
        switch_project(sel)

with st.sidebar.expander("Nowy projekt"):
    np_name = st.text_input("Nazwa projektu", key="np_name")
    if st.button("➕ Utwórz projekt", use_container_width=True, key="create_project_btn"):
        name = (np_name or "").strip()
        if not name:
            st.error("Podaj nazwę projektu.")
        elif db_project_exists(name):
            st.error("Projekt o takiej nazwie już istnieje.")
        else:
            db_save_board(name, DEFAULT_BOARD.model_dump(mode="json"))
            _set_project_id(name)
            st.success("Utworzono projekt.")
            st.session_state.pop("board", None)
            st.rerun()

with st.sidebar.expander("Zmień nazwę projektu"):
    rn_new = st.text_input("Nowa nazwa", key="rn_new")
    if st.button("✏️ Zmień nazwę", use_container_width=True, key="rename_project_btn", disabled=not rn_new.strip()):
        new_name = rn_new.strip()
        old = current_project()
        if not new_name:
            st.error("Podaj nową nazwę.")
        elif db_project_exists(new_name):
            st.error("Projekt o takiej nazwie już istnieje.")
        else:
            ok = db_clone_project(old, new_name)
            if ok:
                _set_project_id(new_name)
                st.success("Zmieniono nazwę projektu.")
                st.session_state.pop("board", None)
                st.rerun()

with st.sidebar.expander("Usuń projekt"):
    del_ok = st.checkbox("Tak, usuń ten projekt", key="confirm_delete_project")
    if st.button("🗑️ Usuń projekt", use_container_width=True, key="delete_project_btn", disabled=not del_ok):
        pid = current_project()
        projects_now = db_list_projects()
        if len(projects_now) <= 1:
            st.error("Nie można usunąć jedynego projektu.")
        else:
            next_pid = next((p for p in projects_now if p != pid), "main")
            db_delete_project(pid)
            _set_project_id(next_pid)
            st.success(f"Usunięto projekt „{pid}”.")
            st.session_state.pop("board", None)
            st.rerun()

# ───────────────────────── SIDEBAR: Status DB + narzędzia ───────────────────────── #
with st.sidebar:
    pid = current_project()
    sb = _sb_client()
    if sb:
        row, status = db_load_board_raw(pid)
        table = _sb_table_name()
        if status == "ok":
            last = row.get("updated_at", "—")
            st.success("Persistencja: Supabase (ON)")
            st.caption(f"Projekt: {pid} | Tabela: {table} | Updated: {last}")
            if st.button("🔁 Force DB reload", use_container_width=True, key="force_db_reload_btn"):
                st.session_state.pop("board", None)
                st.rerun()
        elif status == "not_found":
            st.warning(f"Persistencja: ON, ale brak rekordu dla projektu „{pid}”. Zapis pojawi się po pierwszej zmianie.")
    else:
        st.warning("Persistencja: tylko sesja (OFF) – dodaj SUPABASE_URL/KEY w Secrets.")

# ───────────────────────── SIDEBAR: Filtry + Import/Export + Kolumny ───────────────────────── #
b = get_board()
st.sidebar.header("🔎 Filtry")
title_filter = st.sidebar.text_input("Tytuł zawiera…", key="filter_title")
prio_filter  = st.sidebar.multiselect("Priorytet", options=["Low", "Med", "High"], key="filter_prio")
all_tags     = sorted({tag for task in b.tasks.values() for tag in task.tags})
tags_filter  = st.sidebar.multiselect("Tagi", options=all_tags, key="filter_tags")

st.sidebar.divider(); st.sidebar.header("💾 Import / Export")
export_json_button(b, current_project())
import_json_uploader(current_project())

st.sidebar.divider(); st.sidebar.header("🧱 Kolumny")
with st.sidebar.expander("Dodaj kolumnę"):
    new_col_name = st.text_input("Nazwa nowej kolumny", key="new_col_name")
    if st.button("➕ Dodaj kolumnę", use_container_width=True, key="add_column_btn"):
        if new_col_name and new_col_name.strip():
            add_column(new_col_name.strip()); st.success("Dodano kolumnę."); st.rerun()
        else:
            st.error("Podaj nazwę kolumny.")
with st.sidebar.expander("Zmień nazwę kolumny"):
    col_opts = {c.name: c.id for c in b.columns}
    if col_opts:
        sel_name = st.selectbox("Kolumna", options=list(col_opts.keys()), key="rename_col_sel")
        new_name = st.text_input("Nowa nazwa", key="rename_col_val")
        if st.button("✏️ Zmień nazwę", use_container_width=True, key="rename_column_btn"):
            if new_name and new_name.strip():
                rename_column(col_opts[sel_name], new_name.strip()); st.success("Zmieniono nazwę."); st.rerun()
            else:
                st.error("Podaj nową nazwę.")
with st.sidebar.expander("Usuń kolumnę"):
    col_opts2 = {c.name: c.id for c in b.columns}
    if col_opts2:
        del_name = st.selectbox("Kolumna do usunięcia", options=list(col_opts2.keys()), key="del_col_sel")
        others   = [(c.name, c.id) for c in b.columns if c.name != del_name]
        tgt_name = st.selectbox("Przenieś zadania do…", options=["—"] + [n for n,_ in others], key="move_tasks_to_sel")
        confirm  = st.checkbox("Potwierdzam usunięcie", key="confirm_delete_column")
        if st.button("🗑️ Usuń kolumnę", use_container_width=True, key="delete_column_btn", disabled=not confirm):
            move_to = dict(others).get(tgt_name) if tgt_name != "—" else None
            delete_column(col_opts2[del_name], move_to); st.rerun()

# ───────────────────────── Toolbar + przyciski (jeden modal na raz) ───────────────────────── #
with elements("title"):
    mui.Typography(f"📋 Tablica Kanban — projekt „{current_project()}” — {BUILD_TAG}", variant="h5", gutterBottom=True)

tb1, tb2 = st.columns([0.22, 0.22])
open_add  = tb1.button("➕ Dodaj zadanie", use_container_width=True, key="open_add_btn")
open_edit = tb2.button("✏️ Edytuj zadanie", use_container_width=True, key="open_edit_btn")
if open_add:
    st.session_state["show_add_modal"] = True
    st.session_state["show_edit_modal"] = False
if open_edit:
    st.session_state["show_edit_modal"] = True
    st.session_state["show_add_modal"] = False
if st.session_state.get("show_add_modal") and st.session_state.get("show_edit_modal"):
    st.session_state["show_add_modal"] = False  # preferuj „Edytuj”, jeśli oba True

# ───────────────────────── Modal: Dodaj zadanie ───────────────────────── #
if st.session_state.get("show_add_modal"):
    with _modal("➕ Dodaj zadanie", key="add_modal"):
        b = get_board()
        col_map = {c.name: c.id for c in b.columns}
        with st.form("add_task_form_modal", clear_on_submit=True):
            c = st.columns(2)
            add_title = c[0].text_input("Tytuł*", placeholder="Nazwa zadania", key="add_title")
            add_prio  = c[1].selectbox("Priorytet", ["Low", "Med", "High"], index=1, key="add_prio")
            add_desc  = st.text_area("Opis", placeholder="Krótki opis…", key="add_desc")
            c2 = st.columns(2)
            add_due_enabled = c2[0].checkbox("Ustaw termin", key="add_due_en")
            add_due_val     = c2[0].date_input("Termin", value=date.today(), disabled=not add_due_enabled, key="add_due")
            add_tags_txt    = c2[1].text_input("Tagi (rozdziel przecinkami)", placeholder="ops, ui, backend", key="add_tags")
            add_colname     = st.selectbox("Kolumna docelowa", options=list(col_map.keys()) if col_map else [], key="add_col_sel")
            submitted       = st.form_submit_button("Dodaj", use_container_width=True)
        if submitted:
            if not add_title or not add_title.strip():
                st.error("Tytuł jest wymagany.")
            elif not col_map:
                st.error("Brak kolumn.")
            else:
                tags = [t.strip() for t in add_tags_txt.split(",") if t.strip()]
                due  = add_due_val if add_due_enabled else None
                task = Task(title=add_title.strip(), desc=(add_desc or "").strip(),
                            priority=add_prio, due=due, tags=tags)
                add_task(col_map[add_colname], task)
                st.session_state["show_add_modal"] = False
                st.success("Dodano zadanie."); st.rerun()
        if st.button("Anuluj", type="secondary", key="cancel_add_btn"):
            st.session_state["show_add_modal"] = False; st.rerun()

# ───────────────────────── Modal: Edytuj zadanie ───────────────────────── #
if st.session_state.get("show_edit_modal"):
    with _modal("✏️ Edytuj zadanie", key="edit_modal"):
        b = get_board()
        task_choices = []
        for c in b.columns:
            for tid in c.task_ids:
                t = b.tasks.get(tid)
                if t: task_choices.append((f"{c.name}: {t.title}", tid))
        if not task_choices:
            st.info("Brak zadań do edycji.")
            if st.button("Zamknij", key="close_edit_no_tasks_btn"):
                st.session_state["show_edit_modal"] = False; st.rerun()
        else:
            labels = [lbl for lbl,_ in task_choices]
            selected_label = st.selectbox("Wybierz zadanie", options=labels, key="edit_modal_select")
            selected_tid   = dict(task_choices)[selected_label]
            t = b.tasks[selected_tid]
            current_col_id = next((c.id for c in b.columns if selected_tid in c.task_ids), b.columns[0].id)
            col_map2  = {c.name: c.id for c in b.columns}
            col_names = list(col_map2.keys())
            current_col_name = next(n for n,i in col_map2.items() if i == current_col_id)

            with st.form("edit_task_form_modal", clear_on_submit=True):
                c = st.columns(2)
                etitle = c[0].text_input("Tytuł*", value=t.title, key="edit_title")
                eprio  = c[1].selectbox("Priorytet", ["Low","Med","High"], index=["Low","Med","High"].index(t.priority), key="edit_prio")
                edesc  = st.text_area("Opis", value=t.desc, key="edit_desc")
                c2 = st.columns(2)
                edue_en  = c2[0].checkbox("Ustaw termin", value=t.due is not None, key="edit_due_en")
                edue_val = c2[0].date_input("Termin", value=(t.due or date.today()), disabled=not edue_en, key="edit_due")
                etags    = c2[1].text_input("Tagi (rozdziel przecinkami)", value=", ".join(t.tags), key="edit_tags")
                ecolname = st.selectbox("Kolumna", options=col_names, index=col_names.index(current_col_name), key="edit_col_sel")
                save_btn = st.form_submit_button("Zapisz", use_container_width=True)
            cA, cB, cC = st.columns(3)
            del_click  = cA.button("🗑️ Usuń", use_container_width=True, key="delete_task_btn")
            done_click = cB.button("✅ Done/Undone", use_container_width=True, key="toggle_done_btn")
            cancel_btn = cC.button("Anuluj", type="secondary", use_container_width=True, key="cancel_edit_btn")

            if save_btn:
                if not etitle.strip():
                    st.error("Tytuł jest wymagany.")
                else:
                    updates = {
                        "title": etitle.strip(),
                        "desc": edesc.strip(),
                        "priority": eprio,
                        "due": (edue_val if edue_en else None),
                        "tags": [x.strip() for x in etags.split(",") if x.strip()],
                    }
                    edit_task(selected_tid, updates)
                    new_col_id = col_map2[ecolname]
                    if new_col_id != current_col_id:
                        b2 = get_board()
                        for c in b2.columns:
                            if selected_tid in c.task_ids: c.task_ids.remove(selected_tid)
                        for c in b2.columns:
                            if c.id == new_col_id: c.task_ids.append(selected_tid)
                        save_board(b2)
                    st.session_state["show_edit_modal"] = False
                    st.success("Zapisano zadanie."); st.rerun()
            if del_click:
                delete_task(selected_tid); st.session_state["show_edit_modal"] = False
                st.success("Usunięto zadanie."); st.rerun()
            if done_click:
                edit_task(selected_tid, {"done": not t.done}); st.session_state["show_edit_modal"] = False; st.rerun()
            if cancel_btn:
                st.session_state["show_edit_modal"] = False; st.rerun()

# ───────────────────────── Tablica (DnD) ───────────────────────── #
def pass_filter(t: Task) -> bool:
    ok_title = title_filter.lower() in t.title.lower() if title_filter else True
    ok_prio  = (t.priority in prio_filter) if prio_filter else True
    ok_tags  = (not tags_filter) or (set(tags_filter) & set(t.tags))
    return ok_title and ok_prio and ok_tags

b = get_board()
containers = []
for col in b.columns:
    items = []
    for tid in col.task_ids:
        t = b.tasks.get(tid)
        if not t: continue
        label = item_label_multiline(t) if pass_filter(t) else f"(ukryte filtrem)\n\n\nPriorytet: {t.priority}"
        items.append(encode_item(label, tid))
    containers.append({"header": f"{col.name}", "items": items})

rev = st.session_state.get(REV_KEY, 0)
result = sort_items(containers, multi_containers=True, direction="vertical", key=f"react-kanban-{rev}")

def _extract_items(container_result):
    if container_result is None: return []
    if isinstance(container_result, dict) and "items" in container_result: return container_result["items"]
    if isinstance(container_result, list): return container_result
    for k in ("order", "values"):
        if isinstance(container_result, dict) and k in container_result: return container_result[k]
    return []

if result is not None:
    normalized = [_extract_items(c) for c in result]
    changed = False; b2 = get_board()
    for i, col in enumerate(b2.columns):
        new_ids = [decode_item_id(s) for s in (normalized[i] if i < len(normalized) else [])]
        if new_ids != col.task_ids: col.task_ids = new_ids; changed = True
    if changed: save_board(b2)

st.caption("Projekty w sidebarze (z wyszukiwarką). Panel–modal inline (bez overlay). Import/Export per projekt. Supabase — jeśli skonfigurowano.")
