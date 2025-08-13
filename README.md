# Kanban w Streamlit (drag & drop)

Lekka tablica Kanban w **Streamlit Community Cloud**:
- **Drag & drop** między kolumnami i zmiana kolejności w kolumnie (`streamlit-sortables`).
- Dodawanie, edycja, usuwanie zadań (formularze w **modalach**).
- Zarządzanie kolumnami (dodaj/zmień nazwę/usuń).
- **Filtry** po tytule, tagach, priorytecie.
- **Export/Import JSON** – trwały zapis i odtwarzanie tablicy.

## 1) Uruchomienie lokalne
```bash
pip install -r requirements.txt
streamlit run app.py
```

## 2) Wdrożenie w Streamlit Community Cloud
1. Zrób repo na GitHub z plikami `app.py`, `requirements.txt`, `README.md` i (opcjonalnie) `sample_board.json`.
2. Wejdź na **share.streamlit.io** (Community Cloud) → **New app**.
3. Wybierz repo/branch, wskaż plik startowy **`app.py`** → **Deploy**.

> Komponent DnD: [`streamlit-sortables`](https://github.com/ohtaman/streamlit-sortables)

## 3) Jak używać
- **Dodaj zadanie** – przycisk w sidebarze (formularz w modalu).
- **Edytuj/Usuń** – przyciski na karcie zadania.
- **Przeciąganie** – górny panel „Przeciągnij i upuść…”.
- **Filtry** – tytuł/priorytet/tagi w sidebarze (dotyczą widoku kart, nie DnD).
- **Export JSON** – przycisk w sidebarze (pobiera `board.json`).
- **Import JSON** – wgraj plik w sidebarze (zastępuje aktualną tablicę).

## 4) Struktura danych (JSON)
```json
{
  "columns": [
    {"id": "todo", "name": "Do zrobienia", "task_ids": ["t-1234", "t-5678"]},
    {"id": "inprog", "name": "W trakcie", "task_ids": []},
    {"id": "done", "name": "Zrobione", "task_ids": []}
  ],
  "tasks": {
    "t-1234": {"title": "Zadanie 1", "desc": "", "priority": "High", "due": "2025-08-20", "tags": ["ops"], "done": false},
    "t-5678": {"title": "Zadanie 2", "desc": "", "priority": "Low", "due": "", "tags": [], "done": false}
  }
}
```
- `due` może być puste (`""`) lub w formacie ISO (`YYYY-MM-DD`).
- Import jest walidowany przez **Pydantic** – błędy są czytelnie zgłaszane.

## 5) Ręczna checklista testów
- [ ] Dodaj 3 zadania, edytuj jedno, usuń jedno.
- [ ] Przeciągnij zadania między kolumnami i zmień ich kolejność (panel DnD).
- [ ] Zmień nazwę kolumny i usuń kolumnę z potwierdzeniem (opcjonalnie przenieś zadania).
- [ ] Ustaw filtry po tagach i priorytecie – widok kart się zawęża.
- [ ] Zrób **Export JSON**, odśwież stronę (wyczyść stan), **Import JSON** i sprawdź odtworzenie układu.

## 6) Notatki
**Trwałość danych.** Community Cloud jest efemeryczny – stan zapisuj przez **Export JSON** i potem **Import JSON**.  
**Autoryzacja.** Możesz ustawić aplikację jako prywatną w Community Cloud lub dodać prostą ochronę hasłem przez Secrets.
