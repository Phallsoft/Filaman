# Task 05 — Lookup CRUD (Manufacturers, MaterialTypes, Colors)

## Goal
Manage lookup tables; quick-create without leaving the spool form.

## Deliverables
- `app/routers/lookups.py`: router factory generating CRUD routes for each entity
  - `GET /{entity}` list with inline add form
  - `POST /{entity}` create, `POST /{entity}/{id}` update, `POST /{entity}/{id}/delete`
  - `POST /{entity}/quick` (htmx): create + return refreshed `<option>` list with new item selected
- Shared `lookup_list.html` template with per-entity extra fields (MfgUrl, ColorCode)
- Colors use a color picker input; lists render color swatches
- Delete blocked (friendly error) when in use by a spool.
