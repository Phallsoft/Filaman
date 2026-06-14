# Task 02 — Database models & init

## Goal
SQLAlchemy models matching the REQUEST.MD schema, SQLite storage.

## Deliverables
- `app/database.py`: engine (SQLite file from `DB_PATH`), `SessionLocal`, `get_db` dependency, `init_db()`
- `app/models.py`:
  - `User(id, username unique, hashed_password)`
  - `Manufacturer(id, name unique, mfg_url)`
  - `MaterialType(id, name unique)`
  - `Color(id, name unique, color_code)` — color_code is HTML hex
  - `Spool(id, manufacturer_id FK, material_type_id FK, color_id FK, sku, weight)` — weight in grams
  - `SpoolInventory(spool_id PK/FK, qty)` — enables "3 × black 500g" without per-spool rows
- `create_all` on startup; DB file lives under a mounted volume in Docker.

## Notes
- Unique names on lookup tables; case-insensitive find-or-create helper used by AI import.
- Deleting a spool cascades its inventory row.
