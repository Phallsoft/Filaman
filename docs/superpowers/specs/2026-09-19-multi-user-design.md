# Multi-user Filaman — design

Date: 2026-09-19
Branch: `feature/multi-user`

## Goal

Turn Filaman from one-inventory-per-container into a multi-tenant app: several
users share one container and one SQLite database, each with a fully separate
inventory (spools, manufacturers, materials, colors). An admin user manages
accounts and instance-wide operations.

Decisions already made:

- Accounts are created by an admin; there is no self-registration.
- Lookups (manufacturers, material types, colors) are per-user, not shared.
- Backup / restore / reset stay whole-instance and become admin-only.
- Existing data on the current instance moves to a new user `jpaul`; the
  existing `admin` user keeps the admin role with an empty inventory.

## Data model

### `users`

| column | change |
|---|---|
| `is_admin` | new, `BOOLEAN NOT NULL DEFAULT 0` |

### `manufacturers`, `material_types`, `colors`

| column | change |
|---|---|
| `user_id` | new, `INTEGER NOT NULL REFERENCES users(id)` |
| unique | `(name)` becomes `(user_id, name)` |

### `spools`

| column | change |
|---|---|
| `user_id` | new, `INTEGER NOT NULL REFERENCES users(id)` |
| `uq_spool_identity` | becomes `(user_id, manufacturer_id, material_type_id, color_id, weight, sku)` |

`spool_inventory` is unchanged; it is only reachable through its spool.

Images stay in the flat `images/spools/<uuid>.jpg` layout. Filenames are
random UUIDs and are only referenced from spool rows the owner can see, so a
per-user directory adds nothing.

## Migration

Runs inside `database._migrate_schema()` at startup, guarded by
"`spools.user_id` does not exist". Fresh installs never hit it because
`create_all` builds the final shape.

1. `ALTER TABLE users ADD COLUMN is_admin ...`; set `is_admin = 1` on the
   lowest-id user.
2. **Instance-specific one-off** (clearly labelled, safe to delete later): if
   there is exactly one user and no user named `jpaul`, insert `jpaul` with a
   copy of that user's `hashed_password` and `is_admin = 0`. The data owner
   is `jpaul`. In every other case the owner is the lowest-id user.
3. Rebuild the four data tables, in one transaction with
   `PRAGMA foreign_keys = OFF`: create `<table>_new` with the final schema,
   `INSERT ... SELECT *, <owner_id>`, drop the old table, rename. Order:
   manufacturers, material_types, colors, spools.
4. `PRAGMA foreign_key_check` must return no rows before commit.

`/setup` creates the first user with `is_admin = True`. `reset_db()` is
unchanged (drop everything, back to `/setup`).

## Authentication and authorization

`app/auth.py` gains two FastAPI dependencies:

- `current_user(request, db) -> User` — loads the user from
  `session["user_id"]`. If the row no longer exists (deleted account), clears
  the session and raises a 303 to `/login`.
- `require_admin(user = Depends(current_user)) -> User` — 403 unless
  `user.is_admin`.

Login also stores `session["is_admin"]` so `base.html` can show or hide the
Admin link without a query. Templates receive `username` and `is_admin` from
the same place `username` comes from today.

The auth middleware is unchanged: it still gates unauthenticated access and
first-run setup. Ownership is enforced per route.

## Scoping data access

Every data route takes `user: User = Depends(current_user)`.

- List queries add `.filter(Model.user_id == user.id)`.
- Point lookups use a new helper `get_owned(db, Model, id, user)` that filters
  by id **and** `user_id` and returns `None` when nothing matches, and the route
  handles it exactly as it handles a nonexistent id today (flash + redirect, or
  empty partial). A foreign id therefore looks nonexistent rather than forbidden.
  All `db.get(Model, id)` calls in the routers are replaced.
- Creates pass `user_id=user.id`.
- `lookups.find_or_create(db, model, name, user, **extra)` and
  `spools.merge_or_create_spool(db, user, ...)` include the user in their
  match filters.
- Spool create/edit POSTs verify the submitted `manufacturer_id`,
  `material_type_id`, and `color_id` belong to the user (currently only the FK
  is checked, so a crafted form could attach another user's lookup).
- AI import builds the name lists sent to the LLM from the user's lookups and
  auto-creates missing ones under the user.
- The "in use" check before deleting a lookup stays as is; it is already
  scoped because the lookup itself is user-owned.

## Admin and settings UI

### `/admin` — admin only

Existing sections keep working unchanged: Database Backup, Database Restore,
Reset Database. Change Password moves to `/settings`.

New **Users** section:

- Table: username, role, spool count, actions.
- Actions per row: **Reset password** (form with new password, min 8 chars);
  **Delete** (POST with confirmation phrase = the username). Deleting a user
  removes their spools, inventory rows, lookups, and image files. The admin
  cannot delete their own account.
- **Add user** form: username (unique, trimmed, 1–64 chars) and temporary
  password (min 8 chars). New users are never admins; there is no
  promote/demote UI (out of scope).

### `/settings` — every user

Contains only the Change Password form (moved from `/admin`).

### Navigation

`base.html` shows **Settings** for everyone and **Admin** only when
`is_admin` is true.

## Error handling

- Foreign ids on any data route: same response as a nonexistent id.
- Non-admin on `/admin/*`: 403.
- Deleted user with a live session: session cleared, redirect to `/login`.
- Add user with a taken username: flash error, no change.
- Migration failure: exception propagates and the app does not start; the DB
  transaction rolls back so the old schema is intact.

## Testing

Add `pytest` and `httpx` (dev only, `requirements-dev.txt`). Tests use a
temporary SQLite file per test via `DB_PATH`/`MEDIA_DIR` env and FastAPI's
`TestClient`, driving the real HTTP routes (CSRF token obtained from the
session like the browser does).

Coverage:

- Two users: each sees only their own spools and lookups on list pages.
- Cross-user edit, delete, adjust, and image-proxy on a spool return 404.
- Cross-user edit/delete on a lookup returns 404.
- Spool create with another user's `manufacturer_id` is rejected.
- Merge-on-create only merges with the same user's identical spool.
- AI import save creates lookups under the importing user only.
- Non-admin: `/admin` and every `/admin/*` POST return 403; `/settings`
  works.
- Admin: add user, reset password, delete user (data and image files gone,
  cannot delete self).
- Migration: a fixture DB in the pre-multi-user schema with one `admin` user
  and some rows migrates to `admin` (is_admin, no data) plus `jpaul`
  (not admin, owns every row), with the new unique constraints present.

## Deployment for testing

Dev instance on the server at `/home/jtradmin/filaman-dev`, port 8001,
reachable at `https://filaman-dev.phallsoft.net`. It uses its own compose
project name so it gets its own volume (`filaman-dev_filaman-data`); the dev
volume is seeded from a `scripts/backup.sh` archive of prod so the migration
is exercised on real data before merging.

## Out of scope

- Self-registration or invite links.
- Promoting/demoting admins after setup.
- Per-user export/import.
- Sharing lookups or spools between users.
