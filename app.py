# app.py — Kanban (React UI + smooth DnD + Supabase persist + 4-linie na karcie)
# Wersja: v5.2-supabase-multiline
# - Drag & drop: streamlit-sortables (stabilny klucz, bez zbędnych rerunów)
# - Karty: 4 linie dokładnie: <tytuł>\n<opis>\n<data>\n<Priorytet: X>
# - ID zadania NIE jest pokazywane na kafelku (ukryty separator), ale DnD działa
# - Persistencja: Supabase (PostgreSQL JSONB) + fallback do session_state
# - Sekrety w Streamlit: SUPABASE_URL, SUPABASE_KEY, opcj. BOARD_ID, SUPABASE_TABLE
#
# SQL (domyślna tabela "boards"):
#   create table if not exists public.boards (
#     id text primary key,
#     data jsonb not null,
#     updated_at timestamptz not null default now()
#   );
#   alter table public.boards enable row level security;
#   create policy "boards_read_all"   on public.boards for select using (true);
#   create policy "boards_write_all"  on public.boards for insert with check (true);
#   create policy "boards_update_all" on public.boards for update using (true) with check (true);

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Literal, Optional

import streamlit as st
from pydantic import BaseModel, Field, field_validator, model_validator
from streamlit_sortables import sort_items
from streamlit_elements import elements, mui  # tylko nagłówek/typografia

# --- KONFIG / BUILD TAG ---
BUILD_TAG = "v5.2-supabase-multiline"
REV_KEY = "_view_rev"  # zwiększamy PRZY dodawaniu/import/zmianie kolumn; NIE przy DnD

# ╔════════════════════════════════════════════════════════════════╗
# ║                   SUPABASE – pomocnicze funkcje                ║
# ╚════════════════════════════════════════════════════════════════╝
def _sb_table_name() -> str:
    return st.secrets.get("SUPABASE_TABLE", "boards")


def _sb_client():
    """Zwraca klienta Supabase (v2) lub None (gdy brak pakietu/sektetów)."""
    try:
        from supabase import create_client  # wymagane: supabase>=2.5.0
    except Exception:
        # biblioteka nie zainstalowana – użyjemy wyłącznie session_state
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


def _board_id() -> str:
    return st.secrets.get("BOARD_ID", "main")


def db_load_board_default_insert(default_payload: dict) -> dict | None:
    """Wczytuje słownik stanu z Supabase, a gdy brak – tworzy z domyślnej."""
    sb = _sb_client()
    if not sb:
        return None
    try:
        resp = sb.table(_sb_table_name()).select("data").eq("id", _board_id()).limit(1).execute()
        rows = resp.data or []
        if not rows:
            sb.table(_sb_table_name()).upsert(
                {"id": _board_id(), "data": default_payload, "updated_at": datetime.utcnow().isoformat()}
            ).execute()
            return default_payload
        return rows[0]["data"]
    except Exception as e:
        st.error(f"DB load error: {e}")
        return None


def db_load_board_raw():
    """Zwraca (row, status) do debugowania w sidebarze."""
    sb = _sb_client()
    if not sb:
        return None, "no_client"
    try:
        resp = sb.table(_sb_table_name()).select("*").eq("id", _board_id()).limit(1).execute()
        rows = resp.data or []
        if not rows:
            return None, "not_found"
        return rows[0], "ok"
    except Exception as e:
        return {"error": str(e)}, "error"


def db_save_board(board_dict: dict) -> None:
    """Zapisuje stan do Supabase (upsert)."""
    sb = _sb_client()
    if not sb:
        return
    try:
        payload = {"id": _board_id(), "data": board_dict, "updated_at": datetime.utcnow().isoformat()}
        sb.table(_sb_table_name()).upsert(payload).execute()
        st.session_state["_last_db_save"] = datetime.utcnow().strftime("%H:%M:%S")
    except Exception as e:
        st.error(f"DB save error: {e}")


# ╔════════════════════════════════════════════════════════════════╗
# ║                         MODELE DANYCH                          ║
# ╚════════════════════════════════════════════════════════════════╝
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

# ╔════════════════════════════════════════════════════════════════╗
# ║                        STAN / OPERACJE                         ║
# ╚════════════════════════════════════════════════════════════════╝
def get_board() -> Board:
    # Pierwsze wczytanie: spróbuj DB, inaczej domyślny board
    if "board" not in st.session_state:
        db_data = db_load_board_default_insert(DEFAULT_BOARD.model_dump(mode="json"))
        st.session_state.board = db_data if db_data else DEFAULT_BOARD.model_dump(mode="json")
    return Board(**st.session_state.board)


def save_board(board: Board):
    # Zapis do sesji i do DB (jeśli skonfigurowana)
    as_dict = board.model_dump(mode="json")
    st.session_state.board = as_dict
    db_save_board(as_dict)


def bump_rev():
    # Zmieniamy przy operacjach strukturalnych (dodanie/import/kolumny)
    st.session_state[REV_KEY] = st.session_state.get(REV_KEY, 0) + 1


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
    save_board(b)
    bump_rev()
    return tid


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
    save_board(b)
    bump_rev()
    return cid


def rename_column(column_id: str, new_name: str):
    b = get_board()
    for c in b.columns:
        if c.id == column_id:
            c.name = new_name
            break
    save_board(b)
    bump_rev()


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
    save_board(b)
    bump_rev()


# ╔════════════════════════════════════════════════════════════════╗
# ║                  FORMAT ETYKIETY + UKRYTE ID                   ║
# ╚════════════════════════════════════════════════════════════════╝
def item_label_multiline(t: Task) -> str:
    """
    <nazwa zadania>
    <opis zadania>
    <data zadania>
    <priorytet>
    """
    title = (t.title or "").strip()
    desc = (t.desc or "").strip()
    due = t.due.isoformat() if t.due else ""  # pusty wiersz, gdy brak daty
    prio = f"Priorytet: {t.priority}"
    # ZAWSZE 4 linie (jeśli opis/brak daty – to puste linie)
    return "\n".join([title, desc, due, prio])


# Niewidoczny znak separatora – schowamy za nim ID w tekście karty
_HIDDEN = "\u2063"  # INVISIBLE SEPARATOR


def encode_item(label: str, tid: str) -> str:
    """To, co widzi użytkownik: label (4 linie); za niewidocznym separatorem zapisujemy ID."""
    return f"{label}{_HIDDEN}{tid}"


def decode_item_id(s: str) -> str:
    """Parsuje ID z itemu (obsługuje nowy format i stary '::')."""
    if _HIDDEN in s:
        return s.rsplit(_HIDDEN, 1)[-1]
    if "::" in s:  # zgodność ze starymi buildami
        return s.split("::", 1)[0]
    return s


# ╔════════════════════════════════════════════════════════════════╗
# ║                    IMPORT / EXPORT (JSON)                      ║
# ╚════════════════════════════════════════════════════════════════╝
def export_json_button(board: Board):
    data = board.model_dump(mode="json")
    for _, t in data["tasks"].items():
        if t.get("due") is None:
            t["due"] = ""
    st.download_button(
        "⬇️ Export JSON",
        json.dumps(data, ensure_ascii=False, indent=2),
        "board.json",
        "application/json",
        use_container_width=True,
    )


def import_json_uploader():
    token = st.session_state.get("_import_token", "0")
    up = st.file_uploader("Import JSON (zastąpi bieżącą tablicę)", type=["json"], key=f"import_{token}")
    if up is not None:
        try:
            raw = json.loads(up.read().decode("utf-8"))
            board = Board(**raw)
            save_board(board)  # zapis także do DB
            bump_rev()
            st.success("Zaimportowano tablicę.")
            st.rerun()
        except Exception as e:
            st.error(f"Błąd walidacji importu: {e}")


# ╔════════════════════════════════════════════════════════════════╗
# ║                          UI / LAYOUT                           ║
# ╚════════════════════════════════════════════════════════════════╝
st.set_page_config(page_title="Kanban – React UI", page_icon="🗂️", layout="wide")
st.markdown(
    """
    <style>
      .sortable-container { background: rgba(127,127,127,.08); border-radius: 10px; padding: 10px; min-height: 64px; }
      .sortable-item { background: var(--background-color); border: 1px solid rgba(127,127,127,.35);
                       border-radius: 8px; padding: 8px 10px; margin: 6px 0; font-size: .95rem;
                       color: var(--text-color, #fff);
                       white-space: pre-line; line-height: 1.25;
                       transition: transform .08s ease, background-color .08s ease, box-shadow .08s ease; }
      .sortable-item::first-line { font-weight: 700; } /* pogrub tytuł (1. linia) */
      .block-container { padding-top: .6rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Sidebar – status
with st.sidebar:
    st.info(f"Build: {BUILD_TAG}")
    sb = _sb_client()
    if sb:
        row, status = db_load_board_raw()
        table = _sb_table_name()
        if status == "ok":
            last = row.get("updated_at", "—")
            st.success("Persistencja: Supabase (ON)")
            st.caption(f"Tabela: {table} | Row id: {_board_id()} | Updated: {last}")
            if st.button("🔁 Force DB reload", use_container_width=True):
                st.session_state.pop("board", None)
                st.rerun()
            with st.expander("🧪 Debug: Raw DB row"):
                st.code(json.dumps(row, ensure_ascii=False, indent=2), language="json")
        elif status == "not_found":
            st.warning(f"Persistencja: ON, ale brak rekordu w tabeli '{table}'. Zapis pojawi się po pierwszej zmianie.")
    else:
        st.warning("Persistencja: tylko sesja (OFF) – dodaj SUPABASE_URL/KEY w Secrets.")

# Sidebar – Filtry
b = get_board()
st.sidebar.header("🔎 Filtry")
title_filter = st.sidebar.text_input("Tytuł zawiera…")
prio_filter = st.sidebar.multiselect("Priorytet", options=["Low", "Med", "High"])
all_tags = sorted({tag for task in b.tasks.values() for tag in task.tags})
tags_filter = st.sidebar.multiselect("Tagi", options=all_tags)

# Sidebar – Import/Export
st.sidebar.divider()
st.sidebar.header("💾 Import / Export")
export_json_button(b)
import_json_uploader()

# Sidebar – Kolumny
st.sidebar.divider()
st.sidebar.header("🧱 Kolumny")
with st.sidebar.expander("Dodaj kolumnę"):
    new_col_name = st.text_input("Nazwa nowej kolumny", key="new_col_name")
    if st.button("➕ Dodaj kolumnę", use_container_width=True):
        if new_col_name and new_col_name.strip():
            add_column(new_col_name.strip())
            st.success("Dodano kolumnę.")
            st.rerun()
        else:
            st.error("Podaj nazwę kolumny.")

with st.sidebar.expander("Zmień nazwę kolumny"):
    col_opts = {c.name: c.id for c in b.columns}
    if col_opts:
        sel_name = st.selectbox("Kolumna", options=list(col_opts.keys()), key="rename_sel")
        new_name = st.text_input("Nowa nazwa", key="rename_val")
        if st.button("✏️ Zmień nazwę", use_container_width=True):
            if new_name and new_name.strip():
                rename_column(col_opts[sel_name], new_name.strip())
                st.success("Zmieniono nazwę.")
                st.rerun()
            else:
                st.error("Podaj nową nazwę.")

with st.sidebar.expander("Usuń kolumnę"):
    col_opts2 = {c.name: c.id for c in b.columns}
    if col_opts2:
        del_name = st.selectbox("Kolumna do usunięcia", options=list(col_opts2.keys()), key="del_col_sel")
        others = [(c.name, c.id) for c in b.columns if c.name != del_name]
        tgt_name = st.selectbox("Przenieś zadania do…", options=["—"] + [n for n, _ in others], key="move_to_sel")
        confirm = st.checkbox("Potwierdzam usunięcie")
        if st.button("🗑️ Usuń kolumnę", use_container_width=True, disabled=not confirm):
            move_to = None
            if tgt_name != "—":
                move_to = dict(others)[tgt_name]
            delete_column(col_opts2[del_name], move_to)
            st.rerun()

# Sidebar – Dodawanie zadań
st.sidebar.divider()
with elements("add_task_header"):
    mui.Typography("➕ Dodaj zadanie", variant="h6")
with st.sidebar.form("add_task_form_sidebar", clear_on_submit=True):
    c = st.columns(2)
    add_title = c[0].text_input("Tytuł*", placeholder="Nazwa zadania", key="sb_add_title")
    add_prio = c[1].selectbox("Priorytet", ["Low", "Med", "High"], index=1, key="sb_add_priority")
    add_desc = st.text_area("Opis", placeholder="Krótki opis...", key="sb_add_desc")
    c2 = st.columns(2)
    add_due_enabled = c2[0].checkbox("Ustaw termin", key="sb_add_due_enabled")
    add_due_val = c2[0].date_input("Termin", value=date.today(), disabled=not add_due_enabled, key="sb_add_due_val")
    add_tags_txt = c2[1].text_input("Tagi (rozdziel przecinkami)", placeholder="ops, ui, backend", key="sb_add_tags")
    col_map = {c.name: c.id for c in b.columns}
    add_colname = st.selectbox("Kolumna docelowa", options=list(col_map.keys()) if col_map else [], key="sb_add_col")
    sb_submitted = st.form_submit_button("Dodaj", use_container_width=True)
    if sb_submitted:
        if not add_title or not add_title.strip():
            st.error("Tytuł jest wymagany.")
        elif not col_map:
            st.error("Brak kolumn.")
        else:
            tags = [t.strip() for t in add_tags_txt.split(",") if t.strip()]
            due = add_due_val if add_due_enabled else None
            task = Task(title=add_title.strip(), desc=(add_desc or "").strip(), priority=add_prio, due=due, tags=tags)
            add_task(col_map[add_colname], task)
            st.success("Dodano zadanie.")
            st.rerun()

# Sidebar – Edycja/Usuwanie/Done
with st.sidebar.expander("🛠️ Edycja/Usuwanie zadania"):
    task_choices = []
    for c in b.columns:
        for tid in c.task_ids:
            t = b.tasks.get(tid)
            if t:
                task_choices.append((f"{c.name}: {t.title}", tid))
    if task_choices:
        chosen_label = st.selectbox("Wybierz zadanie", options=[lbl for lbl, _ in task_choices], key="edit_select_task")
        chosen_tid = dict(task_choices)[chosen_label]
        c1, c2, c3 = st.columns(3)
        if c1.button("✏️ Edytuj", use_container_width=True, key="edit_btn"):
            st.session_state.edit_task_id = chosen_tid
            st.rerun()
        if c2.button("🗑️ Usuń", use_container_width=True, key="delete_btn"):
            delete_task(chosen_tid)
            st.success("Usunięto zadanie.")
            st.rerun()
        cur_done = b.tasks[chosen_tid].done
        if c3.button("✅ Done", use_container_width=True, key="toggle_done_btn"):
            edit_task(chosen_tid, {"done": not cur_done})
            st.rerun()

if st.session_state.get("edit_task_id"):
    t = get_board().tasks[st.session_state["edit_task_id"]]
    with st.sidebar.expander("✏️ Edytuj wybrane zadanie", expanded=True):
        with st.form("edit_task_form_sb", clear_on_submit=True):
            ec = st.columns(2)
            etitle = ec[0].text_input("Tytuł*", value=t.title)
            eprio = ec[1].selectbox("Priorytet", ["Low", "Med", "High"], index=["Low", "Med", "High"].index(t.priority))
            edesc = st.text_area("Opis", value=t.desc)
            ec2 = st.columns(2)
            edue_en = ec2[0].checkbox("Ustaw termin", value=t.due is not None)
            edue_val = ec2[0].date_input("Termin", value=(t.due or date.today()), disabled=not edue_en)
            etags_txt = ec2[1].text_input("Tagi (rozdziel przecinkami)", value=", ".join(t.tags))
            col_map2 = {c.name: c.id for c in get_board().columns}
            current_col_id = next(
                (c.id for c in get_board().columns if st.session_state["edit_task_id"] in c.task_ids),
                get_board().columns[0].id,
            )
            col_names2 = list(col_map2.keys())
            current_col_name = next(name for name, cid in col_map2.items() if cid == current_col_id)
            ecolname = st.selectbox("Kolumna", options=col_names2, index=col_names2.index(current_col_name))
            esub = st.form_submit_button("Zapisz", use_container_width=True)
            if esub:
                if not etitle.strip():
                    st.error("Tytuł jest wymagany.")
                else:
                    etags = [x.strip() for x in etags_txt.split(",") if x.strip()]
                    edue = edue_val if edue_en else None
                    edit_task(
                        st.session_state["edit_task_id"],
                        {"title": etitle.strip(), "desc": edesc.strip(), "priority": eprio, "due": edue, "tags": etags},
                    )
                    new_col_id = col_map2[ecolname]
                    if new_col_id != current_col_id:
                        b2 = get_board()
                        for c in b2.columns:
                            if st.session_state["edit_task_id"] in c.task_ids:
                                c.task_ids.remove(st.session_state["edit_task_id"])
                        for c in b2.columns:
                            if c.id == new_col_id:
                                c.task_ids.append(st.session_state["edit_task_id"])
                        save_board(b2)
                    st.success("Zapisano zmiany.")
                    st.session_state.pop("edit_task_id", None)
                    st.rerun()

# Nagłówek (React/MUI)
with elements("title"):
    mui.Typography(f"📋 Tablica Kanban — {BUILD_TAG}", variant="h4", gutterBottom=True)

# Główna tablica (DnD)
def pass_filter(t: Task) -> bool:
    ok_title = title_filter.lower() in t.title.lower() if title_filter else True
    ok_prio = (t.priority in prio_filter) if prio_filter else True
    ok_tags = (not tags_filter) or (set(tags_filter) & set(t.tags))
    return ok_title and ok_prio and ok_tags


b = get_board()
containers = []
for col in b.columns:
    items = []
    for tid in col.task_ids:
        t = b.tasks.get(tid)
        if not t:
            continue
        if pass_filter(t):
            label = item_label_multiline(t)
        else:
            label = f"(ukryte filtrem)\n\n\nPriorytet: {t.priority}"
        items.append(encode_item(label, tid))  # widoczny label, ID ukryte
    containers.append({"header": f"{col.name}", "items": items})

# Stabilny klucz oparty o licznik REV – mniej remountów, brak „migania”
rev = st.session_state.get(REV_KEY, 0)
result = sort_items(containers, multi_containers=True, direction="vertical", key=f"react-kanban-{rev}")

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
    b2 = get_board()
    for i, col in enumerate(b2.columns):
        new_ids = [decode_item_id(s) for s in (normalized[i] if i < len(normalized) else [])]
        if new_ids != col.task_ids:
            col.task_ids = new_ids
            changed = True
    if changed:
        save_board(b2)  # bez st.rerun(); komponent sam odświeży UI

st.caption("Import zastępuje stan, Export pobiera snapshot. Supabase włączona jeśli podałeś sekrety.")
