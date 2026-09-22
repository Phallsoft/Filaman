# Multi-user Filaman Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Several users share one Filaman container, each with a fully separate inventory; an admin account manages users and instance-wide backup/restore/reset.

**Architecture:** Add `user_id` to the four data tables and `is_admin` to `users`; every data route resolves the logged-in `User` via a FastAPI dependency and filters/creates by `user.id`. A one-time SQLite migration rebuilds the data tables with the new columns and constraints and assigns existing rows to a `jpaul` user. Admin-only user management lives on `/admin`; change-password moves to `/settings`.

**Tech Stack:** FastAPI 0.136, SQLAlchemy 2.0, SQLite (stdlib `sqlite3` for the migration), Jinja2, htmx, pytest + Starlette `TestClient` (httpx) for tests.

**Spec:** `docs/superpowers/specs/2026-09-19-multi-user-design.md`

## Global Constraints

- Python 3.12 in the container (`python:3.12-slim`); local venv is 3.14 — write code valid for both.
- Every data route must take `user: User = Depends(current_user)` and never call `db.get(Model, id)` on a user-owned model; use `get_owned`.
- A foreign id is treated exactly like a nonexistent id (same flash/redirect/404 as today), never a 403.
- Lookups unique on `(user_id, name)`; spools unique on `(user_id, manufacturer_id, material_type_id, color_id, weight, sku)`.
- New users are never admins; no promote/demote UI.
- Passwords: min 8 chars everywhere. Usernames: trimmed, 1–64 chars, unique.
- Images stay in flat `images/spools/<uuid>.jpg`.
- Run tests with `.venv/Scripts/python -m pytest -q` (Windows) — every task ends green.
- Commit messages end with the attribution trailer used elsewhere in this repo (`Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` + `Claude-Session:` line).

---

## File map

| File | Responsibility |
|---|---|
| `requirements-dev.txt` (new) | pytest, httpx |
| `tests/conftest.py` (new) | env setup before app import; fresh DB + media dir per test; `TestClient` fixtures |
| `tests/helpers.py` (new) | `csrf()`, `do_setup()`, `login()`, `add_user()`, `add_lookup()`, `add_spool()` HTTP helpers |
| `tests/test_auth_admin.py` (new) | setup/login/admin user management/settings/403 tests |
| `tests/test_scoping.py` (new) | cross-user isolation tests for lookups, spools, AI import save |
| `tests/test_migration.py` (new) | old-schema DB → migrated DB |
| `app/models.py` | `is_admin`, `user_id` columns and constraints |
| `app/auth.py` | `current_user`, `require_admin` dependencies |
| `app/database.py` | `get_owned`; `init_db` calls the migration |
| `app/migrations.py` (new) | `migrate_multi_user(db_path)` |
| `app/services/users.py` (new) | `create_user`, `delete_user` (with data + image cleanup) |
| `app/routers/auth_routes.py` | setup makes admin; login stores `is_admin` |
| `app/routers/admin.py` | admin-only; users section; restore re-runs `init_db` |
| `app/routers/settings.py` (new) | `/settings` change password |
| `app/routers/lookups.py`, `spools.py`, `ai_import.py` | scoped queries |
| `app/templating.py` | `is_admin` in global context |
| `app/templates/admin.html`, `settings.html` (new), `base.html` | UI |
| `app/main.py` | include settings router |
| `docker-compose.yml`, `README.MD` | port variable, no fixed container name, docs |

---

### Task 1: Test harness and a baseline test

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Create: `tests/helpers.py`
- Create: `tests/test_auth_admin.py`
- Modify: `.gitignore` (add `.pytest_cache/`)

**Interfaces:**
- Produces: fixtures `client` (fresh app + DB per test, unauthenticated `TestClient`), `media_dir` (Path); helpers `csrf(client, path)`, `do_setup(client, username, password)`, `login(client, username, password)` — all later tests use these.

- [ ] **Step 1: Add dev requirements and gitignore entry**

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.3
httpx>=0.27
```

Append to `.gitignore`:
```
.pytest_cache/
```

Run: `.venv/Scripts/pip install -r requirements-dev.txt`

- [ ] **Step 2: Write conftest**

`tests/conftest.py`:
```python
"""Test setup. Environment must be configured BEFORE `app` is imported because
app.config/app.database read env at import time."""
import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="filaman-test-"))
os.environ["DB_PATH"] = str(_TMP / "test.db")
os.environ["MEDIA_DIR"] = str(_TMP / "images")
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["AI_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import reset_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def media_dir() -> Path:
    return Path(os.environ["MEDIA_DIR"])


@pytest.fixture()
def client(media_dir):
    reset_db()
    shutil.rmtree(media_dir, ignore_errors=True)
    media_dir.mkdir(parents=True)
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def client2():
    """Second browser (separate cookie jar) against the same app/DB."""
    with TestClient(app, follow_redirects=False) as c:
        yield c
```

- [ ] **Step 3: Write helpers**

`tests/helpers.py`:
```python
import re

_CSRF_RE = re.compile(r'name="csrf_token" value="([0-9a-f]+)"')


def csrf(client, path="/spools") -> str:
    """Fetch a page that contains a form and return the CSRF token in it."""
    r = client.get(path)
    assert r.status_code == 200, f"GET {path} -> {r.status_code}"
    m = _CSRF_RE.search(r.text)
    assert m, f"no csrf token on {path}"
    return m.group(1)


def do_setup(client, username="admin", password="password123"):
    token = csrf(client, "/setup")
    r = client.post("/setup", data={
        "username": username, "password": password, "confirm": password, "csrf_token": token,
    })
    assert r.status_code == 303 and r.headers["location"] == "/spools", r.text
    return r


def login(client, username, password):
    token = csrf(client, "/login")
    r = client.post("/login", data={"username": username, "password": password, "csrf_token": token})
    assert r.status_code == 303 and r.headers["location"] == "/spools", r.text
    return r


def logout(client):
    token = csrf(client, "/spools")
    return client.post("/logout", data={"csrf_token": token})
```

- [ ] **Step 4: Write a baseline test for existing behaviour**

`tests/test_auth_admin.py`:
```python
from tests.helpers import do_setup, login, logout


def test_setup_then_login(client):
    assert client.get("/spools").headers["location"] == "/setup"
    do_setup(client, "admin", "password123")
    assert client.get("/spools").status_code == 200
    logout(client)
    assert client.get("/spools").headers["location"] == "/login"
    login(client, "admin", "password123")
    assert client.get("/spools").status_code == 200
```

- [ ] **Step 5: Run the test**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `1 passed`

- [ ] **Step 6: Commit**

```bash
git add requirements-dev.txt .gitignore tests/
git commit -m "test: add pytest harness with per-test SQLite and HTTP helpers"
```

---

### Task 2: `is_admin`, auth dependencies, settings page, admin user management

**Files:**
- Modify: `app/models.py` (User)
- Modify: `app/auth.py`
- Modify: `app/routers/auth_routes.py`
- Create: `app/services/users.py`
- Create: `app/routers/settings.py`
- Create: `app/templates/settings.html`
- Modify: `app/routers/admin.py`
- Modify: `app/templates/admin.html`
- Modify: `app/templating.py`, `app/templates/base.html`
- Modify: `app/main.py`
- Modify: `tests/helpers.py`, `tests/test_auth_admin.py`

**Interfaces:**
- Produces: `auth.current_user(request, db) -> User`; `auth.require_admin(user) -> User`; `services.users.create_user(db, username, password) -> User` (raises `ValueError` with a user-facing message); `services.users.delete_user(db, user) -> None` (in Task 2 deletes only the user row; Task 4 extends it to delete data + images); helper `add_user(admin_client, username, password)`.
- Routes: `GET /settings`, `POST /settings/password`; `POST /admin/users`, `POST /admin/users/{id}/password`, `POST /admin/users/{id}/delete`. `POST /admin/password` is removed.

- [ ] **Step 1: Write failing tests**

Append to `tests/helpers.py`:
```python
def add_user(admin_client, username, password="password123"):
    token = csrf(admin_client, "/admin")
    r = admin_client.post("/admin/users", data={
        "username": username, "password": password, "csrf_token": token,
    })
    assert r.status_code == 303, r.text
    return r
```

Append to `tests/test_auth_admin.py`:
```python
import sqlite3
import os

from tests.helpers import add_user, csrf


def _user_row(username):
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        return conn.execute(
            "SELECT id, is_admin FROM users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()


def test_setup_user_is_admin_and_sees_admin_nav(client):
    do_setup(client)
    assert _user_row("admin")[1] == 1
    html = client.get("/spools").text
    assert 'href="/admin"' in html
    assert 'href="/settings"' in html


def test_admin_can_add_user_and_new_user_is_not_admin(client, client2):
    do_setup(client)
    add_user(client, "bob", "bobpass123")
    assert _user_row("bob")[1] == 0
    login(client2, "bob", "bobpass123")
    html = client2.get("/spools").text
    assert 'href="/admin"' not in html
    assert 'href="/settings"' in html


def test_add_user_rejects_duplicate_and_short_password(client):
    do_setup(client)
    add_user(client, "bob")
    token = csrf(client, "/admin")
    client.post("/admin/users", data={"username": "bob", "password": "password123", "csrf_token": token})
    client.post("/admin/users", data={"username": "carol", "password": "short", "csrf_token": token})
    assert _user_row("carol") is None
    html = client.get("/admin").text
    assert "already exists" in html or "already taken" in html


def test_non_admin_gets_403_on_admin_routes(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    assert client2.get("/admin").status_code == 403
    token = csrf(client2, "/settings")
    assert client2.post("/admin/users", data={"username": "x", "password": "password123", "csrf_token": token}).status_code == 403
    assert client2.get("/admin/backup").status_code == 403
    assert client2.post("/admin/reset", data={"confirm_text": "", "csrf_token": token}).status_code == 403


def test_settings_change_password(client):
    do_setup(client, "admin", "password123")
    token = csrf(client, "/settings")
    r = client.post("/settings/password", data={
        "current_password": "password123", "new_password": "newpass456",
        "confirm_password": "newpass456", "csrf_token": token,
    })
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    logout(client)
    login(client, "admin", "newpass456")


def test_admin_reset_user_password(client, client2):
    do_setup(client)
    add_user(client, "bob", "password123")
    uid = _user_row("bob")[0]
    token = csrf(client, "/admin")
    r = client.post(f"/admin/users/{uid}/password", data={"new_password": "changed789", "csrf_token": token})
    assert r.status_code == 303
    login(client2, "bob", "changed789")


def test_admin_delete_user_and_cannot_delete_self(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    bob_id = _user_row("bob")[0]
    admin_id = _user_row("admin")[0]
    token = csrf(client, "/admin")
    # wrong confirmation phrase -> no delete
    client.post(f"/admin/users/{bob_id}/delete", data={"confirm_text": "nope", "csrf_token": token})
    assert _user_row("bob") is not None
    client.post(f"/admin/users/{bob_id}/delete", data={"confirm_text": "bob", "csrf_token": token})
    assert _user_row("bob") is None
    # bob's live session is now invalid -> bounced to login
    assert client2.get("/spools").headers["location"] == "/login"
    client.post(f"/admin/users/{admin_id}/delete", data={"confirm_text": "admin", "csrf_token": token})
    assert _user_row("admin") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest -q`
Expected: the 7 new tests FAIL (404s on `/settings`, `/admin/users`, missing `is_admin` column, etc.); `test_setup_then_login` still passes.

- [ ] **Step 3: Model + auth dependencies**

`app/models.py` — change the `User` class and the import line:
```python
from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
...
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
```

`app/auth.py` — add at top and bottom:
```python
import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
```
```python
def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """The logged-in user. If the account no longer exists, drop the session and bounce to /login."""
    user = db.get(User, request.session.get("user_id"))
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user
```

`app/routers/auth_routes.py`:
- In `do_setup`: `user = User(username=username, hashed_password=hash_password(password), is_admin=True)` and after `request.session["username"] = user.username` add `request.session["is_admin"] = True`.
- In `do_login`: after `request.session["username"] = user.username` add `request.session["is_admin"] = bool(user.is_admin)`.

`app/templating.py` — add to `_global_context` dict: `"is_admin": bool(request.session.get("is_admin")),`

`app/templates/base.html` — replace `<li><a href="/admin">Admin</a></li>` with:
```html
    <li><a href="/settings">Settings</a></li>
    {% if is_admin %}<li><a href="/admin">Admin</a></li>{% endif %}
```

- [ ] **Step 4: Users service**

`app/services/users.py`:
```python
from sqlalchemy.orm import Session

from ..auth import hash_password
from ..models import User

MIN_PASSWORD = 8


def validate_password(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    return None


def create_user(db: Session, username: str, password: str, *, is_admin: bool = False) -> User:
    """Create a user or raise ValueError with a message suitable for a flash."""
    username = (username or "").strip()
    if not username or len(username) > 64:
        raise ValueError("Username must be 1–64 characters.")
    err = validate_password(password)
    if err:
        raise ValueError(err)
    if db.query(User.id).filter(User.username == username).first() is not None:
        raise ValueError(f"Username '{username}' already exists.")
    user = User(username=username, hashed_password=hash_password(password), is_admin=is_admin)
    db.add(user)
    db.flush()
    return user


def delete_user(db: Session, user: User) -> None:
    """Delete the account. (Task 4 extends this to remove the user's data and images.)"""
    db.delete(user)
    db.flush()
```

Refactor `do_setup` in `auth_routes.py` to use it (keeps validation in one place):
```python
    username = username.strip()
    if password != confirm:
        flash(request, "Passwords do not match.", "error")
        return templates.TemplateResponse(request, "setup.html", {"form_username": username})
    try:
        user = create_user(db, username, password, is_admin=True)
    except ValueError as e:
        flash(request, str(e), "error")
        return templates.TemplateResponse(request, "setup.html", {"form_username": username})
    db.commit()
```
(import: `from ..services.users import create_user`; remove the now-unused `hash_password` import if nothing else uses it.)

- [ ] **Step 5: Settings router + template**

`app/routers/settings.py`:
```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import current_user, flash, hash_password, verify_csrf, verify_password
from ..database import get_db
from ..models import User
from ..services.users import validate_password
from ..templating import templates

router = APIRouter(prefix="/settings")


@router.get("")
def settings_page(request: Request, user: User = Depends(current_user)):
    return templates.TemplateResponse(request, "settings.html")


@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    err = validate_password(new_password)
    if not verify_password(current_password, user.hashed_password):
        flash(request, "Current password is incorrect.", "error")
    elif err:
        flash(request, err, "error")
    elif new_password != confirm_password:
        flash(request, "New passwords do not match.", "error")
    else:
        user.hashed_password = hash_password(new_password)
        db.commit()
        flash(request, "Password changed.", "success")
    return RedirectResponse("/settings", status_code=303)
```

`app/templates/settings.html` (the Change Password article moved verbatim from `admin.html`, action changed):
```html
{% extends "base.html" %}
{% block title %}Settings — Filaman{% endblock %}
{% block content %}
<h2>Settings</h2>

<article>
  <h4>Change Password</h4>
  <form method="post" action="/settings/password">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Current password
      <input type="password" name="current_password" required autocomplete="current-password">
    </label>
    <div class="grid">
      <label>New password
        <input type="password" name="new_password" required minlength="8" autocomplete="new-password">
      </label>
      <label>Confirm new password
        <input type="password" name="confirm_password" required minlength="8" autocomplete="new-password">
      </label>
    </div>
    <button type="submit">Change Password</button>
  </form>
</article>
{% endblock %}
```

`app/main.py`: import `settings` alongside the other routers and `app.include_router(settings.router)`.

- [ ] **Step 6: Admin router**

Rewrite `app/routers/admin.py` header and add user routes; remove `change_password`:
```python
import datetime
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..auth import flash, hash_password, require_admin, verify_csrf
from ..database import get_db, reset_db
from ..models import Spool, User
from ..services import backup as backup_svc
from ..services.users import create_user, delete_user, validate_password
from ..templating import templates

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

RESET_PHRASE = "Yes, Really Reset The Database"


@router.get("")
def admin_page(request: Request, db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id).all()
    spool_counts = dict(
        db.query(Spool.user_id, func.count(Spool.id)).group_by(Spool.user_id).all()
    ) if hasattr(Spool, "user_id") else {}
    return templates.TemplateResponse(
        request, "admin.html",
        {"reset_phrase": RESET_PHRASE, "users": users, "spool_counts": spool_counts},
    )


@router.post("/users")
def add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    try:
        user = create_user(db, username, password)
    except ValueError as e:
        flash(request, str(e), "error")
        return RedirectResponse("/admin", status_code=303)
    db.commit()
    flash(request, f"User '{user.username}' created.", "success")
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/password")
def reset_user_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    target = db.get(User, user_id)
    err = validate_password(new_password)
    if target is None:
        flash(request, "User not found.", "error")
    elif err:
        flash(request, err, "error")
    else:
        target.hashed_password = hash_password(new_password)
        db.commit()
        flash(request, f"Password reset for '{target.username}'.", "success")
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/delete")
def remove_user(
    request: Request,
    user_id: int,
    confirm_text: str = Form(""),
    csrf_token: str = Form(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    target = db.get(User, user_id)
    if target is None:
        flash(request, "User not found.", "error")
    elif target.id == admin.id:
        flash(request, "You cannot delete your own account.", "error")
    elif confirm_text.strip() != target.username:
        flash(request, f"Delete cancelled — type the username '{target.username}' to confirm.", "error")
    else:
        delete_user(db, target)
        db.commit()
        flash(request, f"User '{target.username}' and all their data were deleted.", "success")
    return RedirectResponse("/admin", status_code=303)
```
Keep `download_backup`, `restore_backup`, `reset_database` exactly as they are. (The `hasattr(Spool, "user_id")` guard is temporary so this task stays green before Task 4 adds the column; Task 4 removes it.)

`app/templates/admin.html` — delete the Change Password `<article>` and insert this as the first article:
```html
<article>
  <h4>Users</h4>
  <figure class="overflow-auto">
    <table>
      <thead>
        <tr><th>Username</th><th>Role</th><th>Spools</th><th class="actions-col"></th></tr>
      </thead>
      <tbody>
        {% for u in users %}
        <tr>
          <td>{{ u.username }}</td>
          <td>{{ "Admin" if u.is_admin else "User" }}</td>
          <td>{{ spool_counts.get(u.id, 0) }}</td>
          <td class="actions">
            <details>
              <summary role="button" class="outline secondary">Reset password</summary>
              <form method="post" action="/admin/users/{{ u.id }}/password">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <fieldset role="group">
                  <input type="password" name="new_password" placeholder="New password" minlength="8" required autocomplete="new-password">
                  <button type="submit">Set</button>
                </fieldset>
              </form>
            </details>
            {% if u.id != request.session.user_id %}
            <details>
              <summary role="button" class="outline danger-button">Delete</summary>
              <form method="post" action="/admin/users/{{ u.id }}/delete">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <p>Deletes <strong>{{ u.username }}</strong> and all their spools and lookups. Type the username to confirm.</p>
                <fieldset role="group">
                  <input name="confirm_text" placeholder="{{ u.username }}" autocomplete="off" required>
                  <button type="submit" class="danger-button">Delete user</button>
                </fieldset>
              </form>
            </details>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </figure>

  <h5>Add user</h5>
  <form method="post" action="/admin/users">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <fieldset role="group">
      <input name="username" placeholder="Username" required maxlength="64" autocomplete="off">
      <input type="password" name="password" placeholder="Temporary password" minlength="8" required autocomplete="new-password">
      <button type="submit">Add user</button>
    </fieldset>
    <small>New users are not admins. Tell them to change the password under Settings.</small>
  </form>
</article>
```

- [ ] **Step 7: Run the tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `8 passed`

- [ ] **Step 8: Commit**

```bash
git add app tests
git commit -m "feat: admin role, user management, and per-user settings page"
```

---

### Task 3: Per-user lookups (manufacturers, materials, colors)

**Files:**
- Modify: `app/models.py` (Manufacturer, MaterialType, Color)
- Modify: `app/database.py` (`get_owned`)
- Modify: `app/routers/lookups.py`
- Modify: `app/routers/ai_import.py` (`_suggestions`, `find_or_create` calls)
- Modify: `app/routers/spools.py` (`_lookup_lists`)
- Modify: `tests/helpers.py`
- Create: `tests/test_scoping.py`

**Interfaces:**
- Produces: `database.get_owned(db, model, item_id, user_id) -> object | None`; `lookups.find_or_create(db, model, name, user_id, **extra) -> (obj|None, created)`; `spools._lookup_lists(db, user_id)`; `ai_import._suggestions(db, user_id)`; helper `add_lookup(client, entity, name, **extra) -> int` (returns the new id, read from the list page).
- Consumes: `current_user` from Task 2.

- [ ] **Step 1: Write failing tests**

Append to `tests/helpers.py`:
```python
import re as _re


def add_lookup(client, entity, name, **extra) -> int:
    """POST a lookup and return its id as rendered on the list page."""
    token = csrf(client, f"/{entity}")
    r = client.post(f"/{entity}", data={"name": name, "csrf_token": token, **extra})
    assert r.status_code == 303, r.text
    html = client.get(f"/{entity}").text
    for row in _re.finditer(r'name="name" value="([^"]*)" form="edit-(\d+)"', html):
        if row.group(1) == name:
            return int(row.group(2))
    raise AssertionError(f"{name} not found on /{entity}")


def lookup_names(client, entity) -> list[str]:
    html = client.get(f"/{entity}").text
    return _re.findall(r'name="name" value="([^"]*)" form="edit-\d+"', html)
```

`tests/test_scoping.py`:
```python
import pytest

from tests.helpers import add_lookup, add_user, csrf, do_setup, login, lookup_names


@pytest.fixture()
def two_users(client, client2):
    """client = admin ('admin'), client2 = 'bob'. Both logged in."""
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    return client, client2


@pytest.mark.parametrize("entity", ["manufacturers", "materials", "colors"])
def test_lookups_are_per_user(two_users, entity):
    a, b = two_users
    add_lookup(a, entity, "Alpha")
    add_lookup(b, entity, "Beta")
    assert lookup_names(a, entity) == ["Alpha"]
    assert lookup_names(b, entity) == ["Beta"]


def test_same_lookup_name_allowed_for_different_users(two_users):
    a, b = two_users
    add_lookup(a, "materials", "PLA")
    add_lookup(b, "materials", "PLA")
    assert lookup_names(a, "materials") == ["PLA"]
    assert lookup_names(b, "materials") == ["PLA"]


def test_cannot_edit_or_delete_other_users_lookup(two_users):
    a, b = two_users
    mid = add_lookup(a, "manufacturers", "Prusa")
    token = csrf(b, "/manufacturers")
    b.post(f"/manufacturers/{mid}", data={"name": "Hacked", "csrf_token": token})
    b.post(f"/manufacturers/{mid}/delete", data={"csrf_token": token})
    assert lookup_names(a, "manufacturers") == ["Prusa"]


def test_quick_create_only_returns_own_items(two_users):
    a, b = two_users
    add_lookup(a, "colors", "Red")
    token = csrf(b, "/colors")
    r = b.post("/colors/quick", data={"qname_colors": "Blue", "csrf_token": token})
    assert "Blue" in r.text and "Red" not in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest -q tests/test_scoping.py`
Expected: FAIL — `test_lookups_are_per_user` sees both names; `test_same_lookup_name_allowed...` hits the unique constraint / "already exists".

- [ ] **Step 3: Models**

In `app/models.py` replace the three lookup classes:
```python
class Manufacturer(Base):
    __tablename__ = "manufacturers"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_manufacturer_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    mfg_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    spools: Mapped[list["Spool"]] = relationship(back_populates="manufacturer")


class MaterialType(Base):
    __tablename__ = "material_types"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_material_type_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    spools: Mapped[list["Spool"]] = relationship(back_populates="material_type")


class Color(Base):
    __tablename__ = "colors"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_color_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color_code: Mapped[str | None] = mapped_column(String(7), nullable=True)  # HTML hex, e.g. #1A2B3C

    spools: Mapped[list["Spool"]] = relationship(back_populates="color")
```

- [ ] **Step 4: `get_owned` helper**

Append to `app/database.py`:
```python
def get_owned(db, model, item_id: int, user_id: int):
    """Fetch a user-owned row by id, or None if it doesn't exist or belongs to someone else."""
    return db.query(model).filter(model.id == item_id, model.user_id == user_id).first()
```

- [ ] **Step 5: Scope the lookups router**

`app/routers/lookups.py`:

Imports: add `from ..auth import current_user, flash, verify_csrf`, `from ..database import get_db, get_owned`, `from ..models import Color, Manufacturer, MaterialType, Spool, User`.

`find_or_create`:
```python
def find_or_create(db: Session, model, name: str, user_id: int, **extra):
    """Case-insensitive find by name within one user's lookups, creating the record if missing."""
    name = (name or "").strip()
    if not name:
        return None, False
    obj = (
        db.query(model)
        .filter(model.user_id == user_id, func.lower(model.name) == name.lower())
        .first()
    )
    if obj:
        return obj, False
    obj = model(name=name, user_id=user_id, **extra)
    db.add(obj)
    db.flush()
    return obj, True
```

Inside `_make_router`, every handler gains `user: User = Depends(current_user)` and:
- `list_items`: `items = db.query(model).filter(model.user_id == user.id).order_by(func.lower(model.name)).all()`
- `create_item`: `find_or_create(db, model, name, user.id, **_clean_extra(...))`
- `quick_create`: same `find_or_create` call; the items query filtered by `model.user_id == user.id`
- `delete_item`: `obj = get_owned(db, model, item_id, user.id)`
- `update_item`: `obj = get_owned(db, model, item_id, user.id)`; the clash query adds `model.user_id == user.id`:
```python
            clash = (
                db.query(model)
                .filter(
                    model.user_id == user.id,
                    func.lower(model.name) == new_name.lower(),
                    model.id != item_id,
                )
                .first()
            )
```

- [ ] **Step 6: Update the other call sites so the app imports and runs**

`app/routers/spools.py`:
```python
def _lookup_lists(db: Session, user_id: int) -> dict:
    return {
        "manufacturers": db.query(Manufacturer).filter(Manufacturer.user_id == user_id).order_by(func.lower(Manufacturer.name)).all(),
        "materials": db.query(MaterialType).filter(MaterialType.user_id == user_id).order_by(func.lower(MaterialType.name)).all(),
        "colors": db.query(Color).filter(Color.user_id == user_id).order_by(func.lower(Color.name)).all(),
    }
```
Add `user: User = Depends(current_user)` to `list_spools`, `new_spool`, `edit_spool` and pass `user.id` to `_lookup_lists`. (Import `current_user` from `..auth` and `User` from `..models`.) Full spool scoping is Task 4; this step only keeps the pages rendering.

`app/routers/ai_import.py`:
```python
def _suggestions(db: Session, user_id: int) -> dict:
    return {
        "manufacturer_names": [m.name for m in db.query(Manufacturer).filter(Manufacturer.user_id == user_id).order_by(func.lower(Manufacturer.name))],
        "material_names": [m.name for m in db.query(MaterialType).filter(MaterialType.user_id == user_id).order_by(func.lower(MaterialType.name))],
        "color_names": [c.name for c in db.query(Color).filter(Color.user_id == user_id).order_by(func.lower(Color.name))],
    }
```
`parse_import` and `save_import` gain `user: User = Depends(current_user)`; `_suggestions(db, user.id)`; the three `find_or_create` calls pass `user.id` as the fourth positional argument:
```python
    mfg, mfg_new = find_or_create(db, Manufacturer, manufacturer, user.id)
    mat, mat_new = find_or_create(db, MaterialType, material, user.id)
    code = (color_hex or "").strip()
    col, col_new = find_or_create(
        db, Color, color_name, user.id, color_code=code if HEX_RE.match(code) else None
    )
```

`app/routers/admin.py`: nothing (the `hasattr` guard still holds).

- [ ] **Step 7: Run all tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `14 passed`

- [ ] **Step 8: Commit**

```bash
git add app tests
git commit -m "feat: scope manufacturers, materials, and colors per user"
```

---

### Task 4: Per-user spools, AI import save, and user deletion cleanup

**Files:**
- Modify: `app/models.py` (Spool)
- Modify: `app/routers/spools.py`
- Modify: `app/routers/ai_import.py` (`merge_or_create_spool` call)
- Modify: `app/routers/admin.py` (drop the `hasattr` guard)
- Modify: `app/services/users.py` (`delete_user` removes data + images)
- Modify: `tests/helpers.py`, `tests/test_scoping.py`, `tests/test_auth_admin.py`

**Interfaces:**
- Produces: `spools.merge_or_create_spool(db, user_id, manufacturer_id, material_type_id, color_id, sku, weight, qty, image_path=None) -> (Spool, merged)`; `spools._lookups_belong_to(db, user_id, manufacturer_id, material_type_id, color_id) -> bool`; helper `add_spool(client, mfg_id, mat_id, col_id, weight=1000, qty=1, sku=None) -> int`.
- Consumes: `get_owned`, `find_or_create(..., user_id, ...)` from Task 3.

- [ ] **Step 1: Write failing tests**

Append to `tests/helpers.py`:
```python
def add_spool(client, mfg_id, mat_id, col_id, weight=1000, qty=1, sku=None) -> int:
    token = csrf(client, "/spools/new")
    data = {
        "manufacturer_id": mfg_id, "material_type_id": mat_id, "color_id": col_id,
        "weight": weight, "qty": qty, "csrf_token": token,
    }
    if sku:
        data["sku"] = sku
    r = client.post("/spools", data=data)
    assert r.status_code == 303 and r.headers["location"] == "/spools", r.text
    ids = spool_ids(client)
    assert ids, "spool not created"
    return ids[-1]


def spool_ids(client) -> list[int]:
    html = client.get("/spools").text
    return sorted({int(x) for x in _re.findall(r'/spools/(\d+)/edit', html)})


def spool_qty(client, spool_id) -> int:
    """Read qty from the edit form (`<input type="number" name="qty" ... value="N">`)."""
    r = client.get(f"/spools/{spool_id}/edit")
    assert r.status_code == 200, f"edit page -> {r.status_code}"
    m = _re.search(r'name="qty"[^>]*value="(\d+)"', r.text)
    assert m, "qty not found"
    return int(m.group(1))
```

Append to `tests/test_scoping.py`:
```python
from tests.helpers import add_spool, spool_ids, spool_qty


def _seed(client):
    """Create one manufacturer/material/color for a client; return their ids."""
    return (
        add_lookup(client, "manufacturers", "Mfg"),
        add_lookup(client, "materials", "PLA"),
        add_lookup(client, "colors", "Black"),
    )


def test_spools_are_per_user(two_users):
    a, b = two_users
    sa = add_spool(a, *_seed(a))
    sb = add_spool(b, *_seed(b))
    assert spool_ids(a) == [sa]
    assert spool_ids(b) == [sb]


def test_cannot_touch_other_users_spool(two_users):
    a, b = two_users
    sa = add_spool(a, *_seed(a), qty=3)
    _seed(b)
    token = csrf(b, "/spools")
    assert b.get(f"/spools/{sa}/edit").status_code in (303, 404)
    b.post(f"/spools/{sa}/adjust", data={"delta": 5, "csrf_token": token})
    b.post(f"/spools/{sa}/delete", data={"csrf_token": token})
    mb, tb, cb = _seed(b)
    b.post(f"/spools/{sa}", data={
        "manufacturer_id": mb, "material_type_id": tb, "color_id": cb,
        "weight": 1, "qty": 0, "csrf_token": token,
    })
    assert spool_ids(a) == [sa]
    assert spool_qty(a, sa) == 3


def test_cannot_create_spool_with_other_users_lookups(two_users):
    a, b = two_users
    ma, ta, ca = _seed(a)
    token = csrf(b, "/spools/new")
    r = b.post("/spools", data={
        "manufacturer_id": ma, "material_type_id": ta, "color_id": ca,
        "weight": 1000, "qty": 1, "csrf_token": token,
    })
    assert r.status_code == 303
    assert spool_ids(b) == []


def test_merge_only_within_user(two_users):
    a, b = two_users
    ma, ta, ca = _seed(a)
    mb, tb, cb = _seed(b)
    s1 = add_spool(a, ma, ta, ca, qty=1)
    s2 = add_spool(a, ma, ta, ca, qty=2)   # identical -> merges into s1
    assert s1 == s2 and spool_qty(a, s1) == 3
    sb = add_spool(b, mb, tb, cb, qty=1)   # same names, different user -> separate
    assert sb != s1 and spool_qty(b, sb) == 1


def test_ai_import_save_creates_lookups_for_importing_user_only(two_users):
    a, b = two_users
    token = csrf(b, "/import")
    r = b.post("/import/save", data={
        "manufacturer": "Polymaker", "material": "PETG", "color_name": "Teal",
        "color_hex": "#008080", "weight": 1000, "qty": 2, "csrf_token": token,
    })
    assert r.status_code == 303 and r.headers["location"] == "/spools"
    assert lookup_names(b, "manufacturers") == ["Polymaker"]
    assert lookup_names(a, "manufacturers") == []
    assert len(spool_ids(b)) == 1 and spool_ids(a) == []
```

Append to `tests/test_auth_admin.py`:
```python
from tests.helpers import add_lookup, add_spool, lookup_names, spool_ids


def test_delete_user_removes_their_data_and_images(client, client2, media_dir):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    m = add_lookup(client2, "manufacturers", "M")
    t = add_lookup(client2, "materials", "T")
    c = add_lookup(client2, "colors", "C")
    sid = add_spool(client2, m, t, c)
    # give the spool an image file on disk
    img_dir = media_dir / "spools"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / "bobpic.jpg").write_bytes(b"x")
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.execute("UPDATE spools SET image_path='spools/bobpic.jpg' WHERE id=?", (sid,))
    conn.commit()
    bob_id = _user_row("bob")[0]
    token = csrf(client, "/admin")
    client.post(f"/admin/users/{bob_id}/delete", data={"confirm_text": "bob", "csrf_token": token})
    assert _user_row("bob") is None
    assert not (img_dir / "bobpic.jpg").exists()
    for table in ("spools", "spool_inventory", "manufacturers", "material_types", "colors"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest -q`
Expected: the new spool/import/delete tests FAIL (spools visible across users; delete leaves rows).

- [ ] **Step 3: Spool model**

In `app/models.py` `Spool`:
```python
    __table_args__ = (
        UniqueConstraint(
            "user_id", "manufacturer_id", "material_type_id", "color_id", "weight", "sku",
            name="uq_spool_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    manufacturer_id: ...
```

- [ ] **Step 4: Spools router**

`app/routers/spools.py` changes:

Imports: `from ..auth import current_user, flash, verify_csrf`; `from ..database import get_db, get_owned`; add `User` to the models import.

Add after `_lookup_lists`:
```python
def _lookups_belong_to(db: Session, user_id: int, manufacturer_id: int, material_type_id: int, color_id: int) -> bool:
    return (
        get_owned(db, Manufacturer, manufacturer_id, user_id) is not None
        and get_owned(db, MaterialType, material_type_id, user_id) is not None
        and get_owned(db, Color, color_id, user_id) is not None
    )
```

`merge_or_create_spool` — new signature and filters:
```python
def merge_or_create_spool(
    db: Session,
    user_id: int,
    manufacturer_id: int,
    material_type_id: int,
    color_id: int,
    sku: str | None,
    weight: int,
    qty: int,
    image_path: str | None = None,
) -> tuple[Spool, bool]:
    """Create a spool, or increment qty on an identical existing one. Returns (spool, merged)."""
    sku = (sku or "").strip() or None
    existing = (
        db.query(Spool)
        .filter(
            Spool.user_id == user_id,
            Spool.manufacturer_id == manufacturer_id,
            Spool.material_type_id == material_type_id,
            Spool.color_id == color_id,
            Spool.weight == weight,
            Spool.sku == sku,
        )
        .first()
    )
    if existing:
        if existing.inventory:
            existing.inventory.qty += qty
        else:
            existing.inventory = SpoolInventory(qty=qty)
        if image_path and not existing.image_path:
            existing.image_path = image_path
        return existing, True
    spool = Spool(
        user_id=user_id,
        manufacturer_id=manufacturer_id,
        material_type_id=material_type_id,
        color_id=color_id,
        sku=sku,
        weight=weight,
        image_path=image_path,
    )
    spool.inventory = SpoolInventory(qty=qty)
    db.add(spool)
    return spool, False
```

Handlers — each gets `user: User = Depends(current_user)`:
- `list_spools`: after the three `.join(...)` calls add `.filter(Spool.user_id == user.id)`; `**_lookup_lists(db, user.id)`.
- `new_spool`: `_lookup_lists(db, user.id)`.
- `create_spool`: before saving the image:
```python
    if not _lookups_belong_to(db, user.id, manufacturer_id, material_type_id, color_id):
        flash(request, "Please choose a manufacturer, material, and color from your lists.", "error")
        return RedirectResponse("/spools/new", status_code=303)
```
  and `merge_or_create_spool(db, user.id, manufacturer_id, material_type_id, color_id, sku, weight, qty, image_path)`.
- `edit_spool`, `update_spool`, `delete_spool`, `adjust_qty`: replace `db.get(Spool, spool_id)` with `get_owned(db, Spool, spool_id, user.id)`. In `update_spool` add the same `_lookups_belong_to` check right after the weight/qty check, redirecting to `f"/spools/{spool_id}/edit"`.
- `image_proxy`: add `user: User = Depends(current_user)` (unused, but it makes the proxy login-only like everything else).

`app/routers/ai_import.py` `save_import`:
```python
    spool, merged = merge_or_create_spool(db, user.id, mfg.id, mat.id, col.id, sku, weight, qty, image_path)
```

`app/routers/admin.py` `admin_page`: replace the `spool_counts` expression with
```python
    spool_counts = dict(db.query(Spool.user_id, func.count(Spool.id)).group_by(Spool.user_id).all())
```

- [ ] **Step 5: User deletion cleans up data**

`app/services/users.py` `delete_user`:
```python
from ..models import Color, Manufacturer, MaterialType, Spool, User
from .images import delete_image


def delete_user(db: Session, user: User) -> None:
    """Delete the account and everything it owns: spools (+inventory, +image files) and lookups."""
    for spool in db.query(Spool).filter(Spool.user_id == user.id).all():
        delete_image(spool.image_path)
        db.delete(spool)  # inventory row cascades via the relationship
    db.flush()
    for model in (Manufacturer, MaterialType, Color):
        db.query(model).filter(model.user_id == user.id).delete(synchronize_session=False)
    db.delete(user)
    db.flush()
```

- [ ] **Step 6: Run all tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `20 passed`

- [ ] **Step 7: Commit**

```bash
git add app tests
git commit -m "feat: scope spools and AI import per user; cascade delete user data"
```

---

### Task 5: Startup migration for existing databases

**Files:**
- Create: `app/migrations.py`
- Modify: `app/database.py` (`init_db`)
- Modify: `app/routers/admin.py` (`restore_backup` re-runs `init_db`)
- Create: `tests/test_migration.py`

**Interfaces:**
- Produces: `migrations.migrate_multi_user(db_path: str) -> bool` (True if the rebuild ran). Idempotent; safe on fresh DBs.
- Consumes: `Base.metadata` table definitions from Tasks 2–4.

- [ ] **Step 1: Write the failing test**

`tests/test_migration.py`:
```python
import sqlite3

from app.migrations import migrate_multi_user

OLD_SCHEMA = """
CREATE TABLE users (
    id INTEGER NOT NULL PRIMARY KEY,
    username VARCHAR(64) NOT NULL UNIQUE,
    hashed_password VARCHAR(128) NOT NULL
);
CREATE TABLE manufacturers (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    mfg_url VARCHAR(512)
);
CREATE TABLE material_types (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(64) NOT NULL UNIQUE
);
CREATE TABLE colors (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(64) NOT NULL UNIQUE,
    color_code VARCHAR(7)
);
CREATE TABLE spools (
    id INTEGER NOT NULL PRIMARY KEY,
    manufacturer_id INTEGER NOT NULL REFERENCES manufacturers (id),
    material_type_id INTEGER NOT NULL REFERENCES material_types (id),
    color_id INTEGER NOT NULL REFERENCES colors (id),
    sku VARCHAR(128),
    weight INTEGER NOT NULL,
    image_path VARCHAR(512),
    CONSTRAINT uq_spool_identity UNIQUE (manufacturer_id, material_type_id, color_id, weight, sku)
);
CREATE TABLE spool_inventory (
    spool_id INTEGER NOT NULL PRIMARY KEY REFERENCES spools (id),
    qty INTEGER NOT NULL
);
"""

OLD_DATA = """
INSERT INTO users (id, username, hashed_password) VALUES (1, 'admin', '$2b$12$hashhashhash');
INSERT INTO manufacturers (id, name, mfg_url) VALUES (1, 'Prusa', 'https://prusa3d.com');
INSERT INTO material_types (id, name) VALUES (1, 'PLA');
INSERT INTO colors (id, name, color_code) VALUES (1, 'Black', '#000000');
INSERT INTO spools (id, manufacturer_id, material_type_id, color_id, sku, weight, image_path)
    VALUES (7, 1, 1, 1, 'SKU-1', 1000, 'spools/abc.jpg');
INSERT INTO spool_inventory (spool_id, qty) VALUES (7, 3);
"""


def _make_old_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA + OLD_DATA)
    conn.close()


def _q(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_migrates_single_user_db_to_admin_plus_jpaul(tmp_path):
    db = str(tmp_path / "old.db")
    _make_old_db(db)

    assert migrate_multi_user(db) is True

    users = _q(db, "SELECT id, username, hashed_password, is_admin FROM users ORDER BY id")
    assert users == [
        (1, "admin", "$2b$12$hashhashhash", 1),
        (2, "jpaul", "$2b$12$hashhashhash", 0),
    ]
    assert _q(db, "SELECT id, user_id, name, mfg_url FROM manufacturers") == [(1, 2, "Prusa", "https://prusa3d.com")]
    assert _q(db, "SELECT id, user_id, name FROM material_types") == [(1, 2, "PLA")]
    assert _q(db, "SELECT id, user_id, name, color_code FROM colors") == [(1, 2, "Black", "#000000")]
    assert _q(db, "SELECT id, user_id, manufacturer_id, sku, weight, image_path FROM spools") == [
        (7, 2, 1, "SKU-1", 1000, "spools/abc.jpg")
    ]
    assert _q(db, "SELECT spool_id, qty FROM spool_inventory") == [(7, 3)]
    # new unique constraints in place, old tables gone
    assert _q(db, "PRAGMA foreign_key_check") == []
    names = {r[0] for r in _q(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(n.endswith("_old") for n in names)
    sql = _q(db, "SELECT sql FROM sqlite_master WHERE name='spools'")[0][0]
    assert "user_id" in sql and "uq_spool_identity" in sql


def test_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "old.db")
    _make_old_db(db)
    migrate_multi_user(db)
    assert migrate_multi_user(db) is False
    assert _q(db, "SELECT count(*) FROM users") == [(2,)]


def test_migration_with_two_users_keeps_data_on_first_user(tmp_path):
    db = str(tmp_path / "old.db")
    _make_old_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO users (id, username, hashed_password) VALUES (2, 'other', 'h')")
    conn.commit()
    conn.close()
    migrate_multi_user(db)
    assert _q(db, "SELECT username FROM users WHERE username='jpaul'") == []
    assert _q(db, "SELECT user_id FROM spools") == [(1,)]
    assert _q(db, "SELECT is_admin FROM users ORDER BY id") == [(1,), (0,)]


def test_fresh_db_is_untouched(tmp_path):
    db = str(tmp_path / "fresh.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, hashed_password TEXT, is_admin BOOLEAN NOT NULL DEFAULT 0)")
    conn.execute("CREATE TABLE spools (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)")
    conn.commit()
    conn.close()
    assert migrate_multi_user(db) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest -q tests/test_migration.py`
Expected: FAIL with `ModuleNotFoundError: app.migrations`

- [ ] **Step 3: Write the migration**

`app/migrations.py`:
```python
"""One-time schema migrations that SQLAlchemy's create_all cannot express.

SQLite cannot add NOT NULL columns with foreign keys or change unique constraints
in place, so tables are rebuilt: rename old -> create new from the ORM metadata ->
copy rows -> drop old.
"""
import sqlite3

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable

from .database import Base

# Tables that gain user_id, in FK-dependency order.
DATA_TABLES = ["manufacturers", "material_types", "colors", "spools"]

# Columns copied from the old table (user_id is appended as a constant).
COPY_COLUMNS = {
    "manufacturers": ["id", "name", "mfg_url"],
    "material_types": ["id", "name"],
    "colors": ["id", "name", "color_code"],
    "spools": ["id", "manufacturer_id", "material_type_id", "color_id", "sku", "weight", "image_path"],
}

# One-off for the original single-user instance: its only account was named
# "admin" but held the real inventory. The data moves to a new "jpaul" account
# (same password) and "admin" keeps the admin role. Safe to delete once run.
LEGACY_DATA_OWNER = "jpaul"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _create_sql(table: str) -> str:
    from . import models  # noqa: F401  (ensure tables are registered)
    return str(CreateTable(Base.metadata.tables[table]).compile(dialect=sqlite_dialect.dialect()))


def migrate_multi_user(db_path: str) -> bool:
    """Add users.is_admin and user_id to the data tables. Returns True if the rebuild ran."""
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # manual transactions
    try:
        if "users" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            return False  # empty DB; create_all will build the final schema
        if "is_admin" not in _columns(conn, "users"):
            conn.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = (SELECT min(id) FROM users)")
        if "user_id" in _columns(conn, "spools"):
            return False

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")  # RENAME must not rewrite FKs in other tables
        conn.execute("BEGIN")
        try:
            first = conn.execute("SELECT id, hashed_password FROM users ORDER BY id LIMIT 1").fetchone()
            owner_id = first[0] if first else None
            user_count = conn.execute("SELECT count(*) FROM users").fetchone()[0]
            has_legacy = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (LEGACY_DATA_OWNER,)
            ).fetchone() is not None
            if user_count == 1 and not has_legacy:
                cur = conn.execute(
                    "INSERT INTO users (username, hashed_password, is_admin) VALUES (?, ?, 0)",
                    (LEGACY_DATA_OWNER, first[1]),
                )
                owner_id = cur.lastrowid

            for table in DATA_TABLES:
                conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
            for table in DATA_TABLES:
                conn.execute(_create_sql(table))
            for table in DATA_TABLES:
                cols = ", ".join(COPY_COLUMNS[table])
                conn.execute(
                    f"INSERT INTO {table} ({cols}, user_id) SELECT {cols}, ? FROM {table}_old",
                    (owner_id,),
                )
            for table in reversed(DATA_TABLES):
                conn.execute(f"DROP TABLE {table}_old")

            problems = conn.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"Migration left dangling foreign keys: {problems[:5]}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return True
    finally:
        conn.close()
```

`app/database.py` `init_db`:
```python
def init_db():
    from . import models  # noqa: F401
    from .migrations import migrate_multi_user

    Base.metadata.create_all(engine)
    _migrate_schema()
    if migrate_multi_user(DB_PATH):
        engine.dispose()  # pooled connections saw the old schema
```
(`_migrate_schema` still adds `image_path` first so `COPY_COLUMNS["spools"]` is valid on the oldest DBs.)

`app/routers/admin.py` `restore_backup`: after `backup_svc.restore_backup(tmp_path)` add `init_db()` (import it from `..database`) so a pre-multi-user backup is migrated immediately instead of at the next restart.

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `24 passed`

- [ ] **Step 5: Commit**

```bash
git add app tests
git commit -m "feat: migrate existing databases to per-user schema"
```

---

### Task 6: Compose/dev deployment settings and docs

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `README.MD`
- Modify: `docs/superpowers/specs/2026-09-19-multi-user-design.md` (get_owned wording)

**Interfaces:** none.

- [ ] **Step 1: Make the port configurable and drop the fixed container name**

`docker-compose.yml`:
```yaml
services:
  filaman:
    build: .
    ports:
      - "${FILAMAN_PORT:-8000}:8000"
    volumes:
      - filaman-data:/data
    environment:
      - DB_PATH=/data/filaman.db
      - SECRET_KEY=${SECRET_KEY:-}
      - AI_BASE_URL=${AI_BASE_URL:-https://openrouter.ai/api/v1}
      - AI_API_KEY=${AI_API_KEY:-}
      - AI_MODEL=${AI_MODEL:-openai/gpt-4o-mini}
      - BRIGHTDATA_MCP_URL=${BRIGHTDATA_MCP_URL:-}
    restart: unless-stopped

volumes:
  filaman-data:
```
(`container_name: filaman` is removed so two checkouts — prod and dev — can run side by side; the scripts already address the container by service name.)

`.env.example` — add after the `DB_PATH` comment:
```
# Host port to publish the app on (container always listens on 8000)
#FILAMAN_PORT=8000
```

- [ ] **Step 2: README**

In the Features list add:
```
- **Multiple users** — each account has its own spools and lookup lists; an admin creates accounts and handles backups
```
In the Configuration table add a row:
```
| `FILAMAN_PORT` | `8000` | Host port to publish the app on |
```
Replace the paragraph starting "The database and spool images live in..." with:
```
The database and spool images live in the `filaman-data` Docker volume. The first account created
at `/setup` is the admin; it adds other users from the Admin page (Settings → change password is
available to everyone). Backups taken from the Admin page are plain SQLite files (no images) and
can be restored from the same page. Upgrading an existing single-user database migrates it
automatically at startup.
```
Add to the Local development section after the venv lines:
```
pip install -r requirements-dev.txt
python -m pytest -q
```

- [ ] **Step 3: Spec wording**

In the spec's "Scoping data access" section, change "raises 404 when nothing matches. A foreign id therefore looks nonexistent rather than forbidden." to "returns `None` when nothing matches, and the route handles it exactly as it handles a nonexistent id today (flash + redirect, or empty partial). A foreign id therefore looks nonexistent rather than forbidden." In "Error handling" change "Foreign ids on any data route: 404." to "Foreign ids on any data route: same response as a nonexistent id."

- [ ] **Step 4: Run tests once more and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `24 passed`

```bash
git add docker-compose.yml .env.example README.MD docs/
git commit -m "chore: configurable host port, multi-user docs"
```

---

### Task 7: Deploy to the dev instance and verify the migration on real data

**Files:** none in the repo (server operations). All commands run over SSH as `jtradmin@10.10.7.208`.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feature/multi-user
```

- [ ] **Step 2: Seed the dev instance from a fresh prod backup**

```bash
ssh jtradmin@10.10.7.208 '
  set -e
  cd /home/jtradmin/filaman && scripts/backup.sh /home/jtradmin/prod-for-dev.tgz
  rm -rf /home/jtradmin/filaman-dev
  git clone -q -b feature/multi-user https://github.com/Phallsoft/Filaman.git /home/jtradmin/filaman-dev
  cd /home/jtradmin/filaman-dev
  cp /home/jtradmin/filaman/.env .env
  printf "\nFILAMAN_PORT=8001\n" >> .env
  scripts/restore.sh /home/jtradmin/prod-for-dev.tgz
  docker compose up -d --build
  sleep 3
  docker compose ps
  docker compose logs --tail 20 filaman
'
```
Expected: volume `filaman-dev_filaman-data` created and restored; container up on 8001; logs show `Application startup complete` and no traceback.

- [ ] **Step 3: Verify the migrated data**

```bash
ssh jtradmin@10.10.7.208 'cd /home/jtradmin/filaman-dev && docker compose exec filaman python -c "
import sqlite3; c = sqlite3.connect(\"/data/filaman.db\")
print(c.execute(\"select id, username, is_admin from users\").fetchall())
print(c.execute(\"select user_id, count(*) from spools group by user_id\").fetchall())
print(c.execute(\"select user_id, count(*) from manufacturers group by user_id\").fetchall())
print(c.execute(\"pragma foreign_key_check\").fetchall())
"'
```
Expected: users `[(1, 'admin', 1), (2, 'jpaul', 0)]`; all 31 spools and every lookup on `user_id = 2`; empty foreign-key check.

- [ ] **Step 4: Smoke test over HTTP**

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://filaman-dev.phallsoft.net/
```
Expected: `303 https://filaman-dev.phallsoft.net/login`. Then in a browser: log in as `jpaul` (admin's password) → 31 spools visible, no Admin link; log in as `admin` → empty inventory, Admin link with Users table listing both accounts.

- [ ] **Step 5: Report**

Tell the user the dev URL, the two accounts, and that prod is untouched. Merging to `main` and redeploying prod is a separate, user-approved step (`superpowers:finishing-a-development-branch`).
