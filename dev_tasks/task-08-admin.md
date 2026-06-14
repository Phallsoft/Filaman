# Task 08 — Admin

## Goal
Password change, DB backup/restore, dangerous reset.

## Deliverables
- `app/services/backup.py`: consistent snapshot via `sqlite3` backup API; restore validation (SQLite header + expected tables)
- `app/routers/admin.py`:
  - `GET /admin`
  - `POST /admin/password`: verify current, set new (min length, confirm match)
  - `GET /admin/backup`: download `filaman-backup-YYYYMMDD-HHMMSS.db`
  - `POST /admin/restore`: upload .db → validate → engine.dispose() → atomic replace → logout (users may differ)
  - `POST /admin/reset`: requires typing exactly `Yes, Really Reset The Database`; drop+recreate schema, clear session → `/setup`
