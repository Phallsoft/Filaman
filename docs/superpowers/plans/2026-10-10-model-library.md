# Model Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-user 3D model library (title-only minimum, optional files/images/metadata), with a category tree, tags, FTS5 search, bulk actions, and read-only sharing with copy-on-add.

**Architecture:** New ORM tables in `app/models.py`; focused services (`file_store`, `library_urls`, `library_taxonomy`, `library_search`, `library`) holding all rules; thin routers (`library`, `library_assets`, `library_admin`) and Jinja/htmx templates in the existing Pico style. Files are content-addressed under `FILES_DIR` and only served through access-checked routes.

**Tech Stack:** FastAPI, SQLAlchemy 2, SQLite (FTS5), Jinja2, htmx, Pico CSS, Pillow, pytest + TestClient.

**Spec:** `docs/superpowers/specs/2026-10-10-model-library-design.md`

**Execution note:** executed natively (night-owl mode). Tests are given in full; for routes and templates the plan fixes the contract (paths, form field names, markup hooks the tests rely on) and the implementation follows the existing spool/lookup patterns.

## Global Constraints

- Only `title` is required on a model; every other field is optional.
- Every model/category/tag query is scoped by `user_id`, except read access to `is_shared` models via `get_viewable`.
- Every POST calls `verify_csrf(request, csrf_token)`.
- Blobs live at `FILES_DIR/<sha256[:2]>/<sha256>`; never derive a disk path from a user filename.
- `FILES_DIR` default: `Path(DB_PATH).resolve().parent / "files"`; `MODEL_MAX_FILE_MB` default `250`.
- Table names prefixed `library_` (plus `stored_files`); FTS table `library_search`.
- Model routes use `{model_id:int}` path convertors so `/models/shared`, `/models/new`, etc. never collide.
- No new runtime dependencies.
- Library list page size: 60.

## Review Focus

1. **FTS syntax in user search input** (`"`, `*`, `AND`, `(`, `-`): must return results or none, never a 500. → Task 4 `test_search_input_with_fts_syntax_does_not_error`.
2. **Browser sends an empty file part when no file is chosen** (`filename=""`): must be ignored, not stored as an empty blob. → Task 6 `test_empty_file_part_is_ignored`.
3. **Two models referencing the same blob, one deleted** (copy then delete original): the copy's download must still work. → Task 7 `test_copy_survives_original_delete_and_download_works`.
4. **Bulk action with foreign or nonexistent IDs**: silently ignored, owned ones still processed. → Task 8 `test_bulk_ignores_foreign_ids`.
5. **Hostile download filename** (`../../etc/passwd`, quotes, newlines): stored and served as a sanitised attachment name; disk path unaffected. → Task 6 `test_download_filename_is_sanitised`.

---

### Task 1: Config, ORM tables, content-addressed file store

**Files:**
- Modify: `app/config.py`, `app/models.py`, `tests/conftest.py`
- Create: `app/services/file_store.py`, `tests/test_file_store.py`

**Interfaces:**
- Produces:
  - `config.FILES_DIR: str`, `config.MODEL_MAX_FILE_MB: int`
  - ORM: `LibraryCategory(id, user_id, parent_id, name)`, `LibraryTag(id, user_id, name)`, `library_model_tags` Table, `StoredFile(id, sha256, size, content_type)`, `LibraryModel(...)` with relationships `tags` (ordered by name), `files` / `images` (ordered by position, cascade delete-orphan), `category`, `owner`, and property `cover`, `LibraryModelFile(id, model_id, stored_file_id, filename, position, stored)`, `LibraryModelImage(id, model_id, stored_file_id, position, stored)`.
  - `file_store.FileTooLarge(Exception)` with `.filename`
  - `file_store.files_root() -> Path`
  - `file_store.blob_path(sha256: str) -> Path`
  - `async file_store.store_upload(db, upload: UploadFile, max_bytes: int) -> StoredFile`
  - `file_store.store_bytes(db, data: bytes, content_type: str) -> StoredFile`
  - `file_store.exists(stored: StoredFile) -> bool`
  - `file_store.release(db, ids: Iterable[int]) -> None`
  - `file_store.clear_all() -> None`

- [ ] **Step 1: conftest** — set `os.environ["FILES_DIR"] = str(_TMP / "files")` before importing the app; add a `files_dir` fixture; the `client` fixture also wipes and recreates it.

- [ ] **Step 2: Write failing tests** `tests/test_file_store.py`:

```python
import asyncio
import hashlib
from io import BytesIO

import pytest
from fastapi import UploadFile

from app.database import SessionLocal
from app.models import LibraryModel, LibraryModelFile, StoredFile, User
from app.services import file_store
from tests.helpers import do_setup


def _upload(data: bytes, name="part.stl") -> UploadFile:
    return UploadFile(file=BytesIO(data), filename=name)


def _store(db, data: bytes, max_bytes=10_000):
    return asyncio.run(file_store.store_upload(db, _upload(data), max_bytes))


def test_store_upload_writes_blob_by_hash(client, files_dir):
    with SessionLocal() as db:
        stored = _store(db, b"solid cube")
        db.commit()
        sha = hashlib.sha256(b"solid cube").hexdigest()
        assert stored.sha256 == sha and stored.size == 10
        assert file_store.blob_path(sha) == files_dir / sha[:2] / sha
        assert file_store.blob_path(sha).read_bytes() == b"solid cube"


def test_duplicate_upload_is_stored_once(client):
    with SessionLocal() as db:
        a = _store(db, b"same bytes")
        b = _store(db, b"same bytes")
        db.commit()
        assert a.id == b.id
        assert db.query(StoredFile).count() == 1


def test_too_large_upload_raises_and_leaves_no_temp(client, files_dir):
    with SessionLocal() as db:
        with pytest.raises(file_store.FileTooLarge) as exc:
            _store(db, b"x" * 101, max_bytes=100)
        assert exc.value.filename == "part.stl"
        assert db.query(StoredFile).count() == 0
    tmp = files_dir / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


def _model_with_file(db, user_id, stored, title="M"):
    m = LibraryModel(user_id=user_id, title=title)
    m.files.append(LibraryModelFile(stored_file_id=stored.id, filename="a.stl", position=0))
    db.add(m)
    db.flush()
    return m


def test_release_keeps_blob_while_referenced(client):
    do_setup(client)
    with SessionLocal() as db:
        uid = db.query(User).first().id
        stored = _store(db, b"shared blob")
        m1 = _model_with_file(db, uid, stored, "one")
        _model_with_file(db, uid, stored, "two")
        db.delete(m1)
        db.flush()
        file_store.release(db, [stored.id])
        db.commit()
        assert db.get(StoredFile, stored.id) is not None
        assert file_store.blob_path(stored.sha256).exists()


def test_release_deletes_unreferenced_blob(client):
    do_setup(client)
    with SessionLocal() as db:
        uid = db.query(User).first().id
        stored = _store(db, b"lonely blob")
        m = _model_with_file(db, uid, stored)
        sha = stored.sha256
        db.delete(m)
        db.flush()
        file_store.release(db, [stored.id])
        db.commit()
        assert db.query(StoredFile).count() == 0
        assert not file_store.blob_path(sha).exists()


def test_store_bytes_and_clear_all(client, files_dir):
    with SessionLocal() as db:
        stored = file_store.store_bytes(db, b"\xff\xd8jpeg", "image/jpeg")
        db.commit()
        assert file_store.exists(stored)
    file_store.clear_all()
    assert not any(p for p in files_dir.rglob("*") if p.is_file())
```

- [ ] **Step 3: Run** `python -m pytest tests/test_file_store.py -q` → FAIL (import errors).

- [ ] **Step 4: Implement** config vars, ORM classes (timestamps `default=_utcnow`, `onupdate=_utcnow`), and `file_store`:
  - `store_upload`: `files_root()/tmp/<uuid4>`; read `await upload.read(1 << 20)` in a loop, update `hashlib.sha256`, count bytes; on exceeding `max_bytes` close + unlink temp, raise `FileTooLarge(upload.filename)`. If a `StoredFile` with that hash exists, unlink temp and return it. Else `os.replace` to `blob_path`, insert row (content type from `upload.content_type` or `mimetypes.guess_type`, fallback `application/octet-stream`), `db.flush()`.
  - `release`: `db.flush()`; for each id, if no `LibraryModelFile` and no `LibraryModelImage` references it, unlink the blob (missing_ok) and delete the row.
  - `clear_all`: `shutil.rmtree(files_root())` then recreate.

- [ ] **Step 5: Run** the file store tests and the full suite → PASS.

- [ ] **Step 6: Commit** `feat(library): add library tables and content-addressed file store`.

---

### Task 2: URL helpers (title from URL, platform)

**Files:**
- Create: `app/services/library_urls.py`, `tests/test_library_urls.py`

**Interfaces:**
- Produces: `is_http_url(url: str) -> bool`, `title_from_url(url: str) -> str | None`, `platform_for(url: str) -> str | None`.

- [ ] **Step 1: Write failing tests**

```python
import pytest

from app.services.library_urls import is_http_url, platform_for, title_from_url


@pytest.mark.parametrize("url,expected", [
    ("https://www.printables.com/model/123456-wera-bit-holder", "Wera bit holder"),
    ("https://www.printables.com/model/123456-wera-bit-holder/files", "Wera bit holder"),
    ("https://makerworld.com/en/models/987654-articulated-dragon#profileId-1", "Articulated dragon"),
    ("https://cults3d.com/en/3d-model/home/cable_clip_v2", "Cable clip v2"),
    ("https://example.com/downloads/Gear%20Box.stl", "Gear Box"),
    ("https://www.thingiverse.com/thing:4567890", None),
    ("https://example.com/", None),
    ("https://example.com/12345", None),
    ("not a url", None),
])
def test_title_from_url(url, expected):
    assert title_from_url(url) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://www.printables.com/model/1-x", "Printables"),
    ("https://thingiverse.com/thing:1", "Thingiverse"),
    ("https://makerworld.com/en/models/1", "MakerWorld"),
    ("https://cults3d.com/en/3d-model/x", "Cults"),
    ("https://www.myminifactory.com/object/x", "MyMiniFactory"),
    ("https://thangs.com/x", "Thangs"),
    ("https://grabcad.com/library/x", "GrabCAD"),
    ("https://github.com/a/b", "GitHub"),
    ("https://www.example.org/x", "example.org"),
    ("ftp://example.org/x", None),
    ("", None),
])
def test_platform_for(url, expected):
    assert platform_for(url) == expected


def test_is_http_url():
    assert is_http_url("https://a.b/c") and is_http_url("http://a.b")
    assert not is_http_url("javascript:alert(1)") and not is_http_url("a.b") and not is_http_url("")
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** per spec (generic segments: `model, models, 3d-model, thing, things, files, file, download, downloads, object, library, en, de, fr, es, it, home, design, designs`; strip leading `^\d+[-_]`; drop extensions `stl|3mf|obj|step|stp|zip|gcode|f3d`; unquote; replace `[-_+]` with space; skip purely numeric; capitalise first character only). **Step 4: Run** → PASS. **Step 5: Commit** `feat(library): derive titles and platforms from model URLs`.

---

### Task 3: Taxonomy service (categories and tags)

**Files:**
- Create: `app/services/library_taxonomy.py`, `tests/test_library_taxonomy.py`

**Interfaces:**
- Consumes: ORM from Task 1.
- Produces:
  - `class TaxonomyError(ValueError)`
  - `user_categories(db, user_id) -> list[LibraryCategory]`
  - `descendant_ids(categories, root_id: int) -> set[int]` (includes root)
  - `category_path(by_id: dict[int, LibraryCategory], cat_id: int | None) -> str` (`"A › B"`, `""` for None)
  - `tree_rows(categories, direct_counts: dict[int | None, int]) -> list[dict]` — depth-first by lowercase name; each `{"category", "depth", "count", "path"}` where `count` includes descendants
  - `create_category(db, user_id, name, parent_id=None) -> LibraryCategory`
  - `rename_category(db, cat, name) -> None`
  - `move_category(db, cat, new_parent_id: int | None) -> None`
  - `delete_category(db, cat) -> None` (models → Uncategorized, children → cat.parent_id)
  - `owned_category(db, user_id, raw: str | int | None) -> LibraryCategory | None`
  - `parse_tag_names(text: str | None) -> list[str]` (comma-separated, trimmed, ≤64 chars, case-insensitive de-dup, order kept)
  - `get_or_create_tags(db, user_id, names) -> list[LibraryTag]`
  - `rename_tag(db, tag, new_name) -> LibraryTag` (merges into an existing same-name tag; returns surviving tag)
  - `affected_model_ids_for_categories(db, cat_ids) -> list[int]`

- [ ] **Step 1: Write failing tests**

```python
import pytest

from app.database import SessionLocal
from app.models import LibraryModel, LibraryTag, User
from app.services import library_taxonomy as tx
from tests.helpers import do_setup


@pytest.fixture()
def db(client):
    do_setup(client)
    with SessionLocal() as session:
        yield session


def _uid(db):
    return db.query(User).first().id


def test_create_rejects_duplicate_sibling_case_insensitive(db):
    uid = _uid(db)
    tx.create_category(db, uid, "Workshop")
    with pytest.raises(tx.TaxonomyError):
        tx.create_category(db, uid, "workshop")
    parent = tx.create_category(db, uid, "Toys")
    tx.create_category(db, uid, "Workshop", parent.id)  # different parent is fine


def test_tree_rows_counts_include_descendants(db):
    uid = _uid(db)
    a = tx.create_category(db, uid, "A")
    b = tx.create_category(db, uid, "B", a.id)
    c = tx.create_category(db, uid, "C", b.id)
    rows = tx.tree_rows(tx.user_categories(db, uid), {a.id: 1, b.id: 2, c.id: 3})
    assert [(r["category"].name, r["depth"], r["count"]) for r in rows] == [
        ("A", 0, 6), ("B", 1, 5), ("C", 2, 3)
    ]
    assert rows[2]["path"] == "A › B › C"


def test_move_into_own_descendant_rejected(db):
    uid = _uid(db)
    a = tx.create_category(db, uid, "A")
    b = tx.create_category(db, uid, "B", a.id)
    with pytest.raises(tx.TaxonomyError):
        tx.move_category(db, a, b.id)
    with pytest.raises(tx.TaxonomyError):
        tx.move_category(db, a, a.id)
    tx.move_category(db, b, None)
    assert b.parent_id is None


def test_delete_category_rehomes_models_and_children(db):
    uid = _uid(db)
    a = tx.create_category(db, uid, "A")
    b = tx.create_category(db, uid, "B", a.id)
    c = tx.create_category(db, uid, "C", b.id)
    m = LibraryModel(user_id=uid, title="m", category_id=b.id)
    db.add(m)
    db.flush()
    tx.delete_category(db, b)
    db.flush()
    assert m.category_id is None
    assert c.parent_id == a.id


def test_parse_tag_names():
    assert tx.parse_tag_names(" gift, Printed ,gift,, needs-supports ") == ["gift", "Printed", "needs-supports"]
    assert tx.parse_tag_names(None) == []


def test_rename_tag_merges_on_collision(db):
    uid = _uid(db)
    gift, present = tx.get_or_create_tags(db, uid, ["gift", "present"])
    m1 = LibraryModel(user_id=uid, title="one", tags=[gift])
    m2 = LibraryModel(user_id=uid, title="two", tags=[gift, present])
    db.add_all([m1, m2])
    db.flush()
    survivor = tx.rename_tag(db, present, "Gift")
    db.flush()
    assert survivor.id == gift.id
    assert db.query(LibraryTag).filter_by(user_id=uid).count() == 1
    assert [t.name for t in m1.tags] == ["gift"] and [t.name for t in m2.tags] == ["gift"]
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(library): category tree and tag services`.

---

### Task 4: FTS5 search index

**Files:**
- Create: `app/services/library_search.py`, `tests/test_library_search.py`
- Modify: `app/database.py` (`init_db` / `reset_db` call `ensure_index`; `reset_db` drops `library_search` first)

**Interfaces:**
- Consumes: Task 1 ORM, Task 3 `user_categories`, `category_path`.
- Produces: `ensure_index(engine) -> None`, `reindex(db, model_ids: Iterable[int]) -> None`, `fts_query(q: str) -> str | None`, `search_ids(db, q: str) -> set[int] | None` (None when `q` is blank).

- [ ] **Step 1: Write failing tests**

```python
from app.database import SessionLocal, engine, init_db
from app.models import LibraryModel, User
from app.services import library_search as ls
from app.services import library_taxonomy as tx
from tests.helpers import do_setup


def _setup(client):
    do_setup(client)
    db = SessionLocal()
    return db, db.query(User).first().id


def test_search_matches_title_tag_and_category_path(client):
    db, uid = _setup(client)
    cat = tx.create_category(db, uid, "Workshop")
    sub = tx.create_category(db, uid, "Holders", cat.id)
    m = LibraryModel(user_id=uid, title="Bit rack", category_id=sub.id,
                     tags=tx.get_or_create_tags(db, uid, ["gift"]))
    other = LibraryModel(user_id=uid, title="Dragon")
    db.add_all([m, other])
    db.flush()
    ls.reindex(db, [m.id, other.id])
    db.commit()
    assert ls.search_ids(db, "bit") == {m.id}
    assert ls.search_ids(db, "gift") == {m.id}
    assert ls.search_ids(db, "workshop") == {m.id}
    assert ls.search_ids(db, "drag") == {other.id}
    assert ls.search_ids(db, "   ") is None
    db.close()


def test_category_rename_reflected_after_reindex(client):
    db, uid = _setup(client)
    cat = tx.create_category(db, uid, "Toys")
    m = LibraryModel(user_id=uid, title="Top", category_id=cat.id)
    db.add(m)
    db.flush()
    ls.reindex(db, [m.id])
    tx.rename_category(db, cat, "Fidgets")
    ls.reindex(db, tx.affected_model_ids_for_categories(db, [cat.id]))
    db.commit()
    assert ls.search_ids(db, "fidgets") == {m.id}
    assert ls.search_ids(db, "toys") == set()
    db.close()


def test_search_input_with_fts_syntax_does_not_error(client):
    db, uid = _setup(client)
    m = LibraryModel(user_id=uid, title='Hook "XL"')
    db.add(m)
    db.flush()
    ls.reindex(db, [m.id])
    db.commit()
    for q in ['"', '*', 'AND', '(hook', 'hook -', 'NEAR(', "x' OR 1=1 --"]:
        ls.search_ids(db, q)  # must not raise
    assert ls.search_ids(db, '"hook"') == {m.id}
    db.close()


def test_init_db_rebuilds_missing_index(client):
    db, uid = _setup(client)
    m = LibraryModel(user_id=uid, title="Orphan index")
    db.add(m)
    db.commit()
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE library_search")
    init_db()
    assert ls.search_ids(db, "orphan") == {m.id}
    db.close()
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** `fts_query`: `re.findall(r"\w+", q)` → `" AND ".join(f'"{w}"*')`; empty → None. `ensure_index`: `CREATE VIRTUAL TABLE IF NOT EXISTS ...`; if `count(library_search) != count(library_models)`, delete all and reindex every model. **Step 4: Run** full suite → PASS. **Step 5: Commit** `feat(library): FTS5 search index`.

---

### Task 5: Library core pages (list, quick-add, create, detail, edit, delete, share)

**Files:**
- Create: `app/services/library.py`, `app/routers/library.py`, `app/templates/library/list.html`, `library/form.html`, `library/detail.html`, `library/_card.html`, `library/_sidebar.html`, `tests/test_library_pages.py`
- Modify: `app/main.py` (include router), `app/templates/base.html` (Models nav link), `app/static/custom.css`, `tests/helpers.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `library.get_viewable(db, model_id, user) -> LibraryModel | None`
  - `library.set_tags(db, model, names: list[str]) -> None`
  - `library.apply_fields(model, *, title, source_url, platform, author, license, description, print_settings) -> str | None` (returns error message; fills platform/title fallbacks)
  - `async library.attach_uploads(db, model, files: list[UploadFile], images: list[UploadFile], image_url: str | None) -> list[str]` (error messages)
  - `library.delete_model(db, model) -> None` (clears copies' `copied_from_id`, removes index row, releases blobs)
  - `library.copy_model(db, src, user, category_id, tag_names) -> LibraryModel`
  - `images.normalize_photo(raw: bytes, max_side: int = 1600) -> bytes` (added to `app/services/images.py`)
  - Test helpers: `add_model(client, title="", url="", **fields) -> int`, `model_ids(client, path="/models") -> list[int]`.
  - Markup hooks: each card links `href="/models/{id}"`; form fields named `title, source_url, platform, author, license, description, print_settings, category_id, new_category, tags, files, images, image_url`.

- [ ] **Step 1: Helpers** in `tests/helpers.py`:

```python
def add_model(client, title="", url="", files=None, **fields) -> int:
    token = csrf(client, "/models/new")
    data = {"title": title, "source_url": url, "csrf_token": token, **fields}
    r = client.post("/models", data=data, files=files or None)
    assert r.status_code == 303, r.text
    m = re.match(r"/models/(\d+)$", r.headers["location"])
    assert m, f"create redirected to {r.headers['location']}"
    return int(m.group(1))


def model_ids(client, path="/models") -> list[int]:
    html = client.get(path).text
    return sorted({int(x) for x in re.findall(r'href="/models/(\d+)"', html)})
```

- [ ] **Step 2: Write failing tests** `tests/test_library_pages.py`:

```python
import re

import pytest

from tests.helpers import add_model, add_user, csrf, do_setup, login, model_ids


@pytest.fixture()
def two_users(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    return client, client2


def test_nav_has_models_link(client):
    do_setup(client)
    assert 'href="/models"' in client.get("/spools").text


def test_create_with_title_only(client):
    do_setup(client)
    mid = add_model(client, title="Benchy")
    r = client.get(f"/models/{mid}")
    assert r.status_code == 200 and "Benchy" in r.text
    assert model_ids(client) == [mid]


def test_title_derived_from_url_and_platform_filled(client):
    do_setup(client)
    mid = add_model(client, url="https://www.printables.com/model/123-wera-bit-holder")
    page = client.get(f"/models/{mid}").text
    assert "Wera bit holder" in page and "Printables" in page


def test_missing_title_and_no_derivable_title_rejected(client):
    do_setup(client)
    token = csrf(client, "/models/new")
    r = client.post("/models", data={"title": "", "source_url": "https://www.thingiverse.com/thing:1", "csrf_token": token})
    assert r.status_code == 303 and r.headers["location"].startswith("/models/new")
    assert model_ids(client) == []


def test_non_http_url_rejected(client):
    do_setup(client)
    token = csrf(client, "/models/new")
    r = client.post("/models", data={"title": "x", "source_url": "javascript:alert(1)", "csrf_token": token})
    assert r.headers["location"].startswith("/models/new")
    assert model_ids(client) == []


def test_quick_add_into_category(client):
    do_setup(client)
    token = csrf(client, "/models/categories")
    client.post("/models/categories", data={"name": "Toys", "csrf_token": token})
    cat_id = int(re.search(r'href="/models\?cat=(\d+)"', client.get("/models").text).group(1))
    token = csrf(client, "/models")
    r = client.post("/models/quick", data={
        "url": "https://makerworld.com/en/models/55-fidget-spinner", "title": "", "category_id": cat_id,
        "csrf_token": token,
    })
    assert r.status_code == 303
    assert "Fidget spinner" in client.get(f"/models?cat={cat_id}").text
    assert "Fidget spinner" not in client.get("/models?cat=none").text


def test_edit_updates_fields_and_tags(client):
    do_setup(client)
    mid = add_model(client, title="Old")
    token = csrf(client, f"/models/{mid}/edit")
    r = client.post(f"/models/{mid}", data={
        "title": "New", "author": "Ada", "tags": "gift, printed", "print_settings": "0.2mm\n15% infill",
        "csrf_token": token,
    })
    assert r.status_code == 303
    page = client.get(f"/models/{mid}").text
    assert "New" in page and "Ada" in page and "gift" in page and "printed" in page
    assert "15% infill" in page


def test_tag_filter_and_search(client):
    do_setup(client)
    a = add_model(client, title="Alpha", tags="gift")
    b = add_model(client, title="Beta")
    tag_id = int(re.search(r'tag=(\d+)', client.get("/models").text).group(1))
    assert model_ids(client, f"/models?tag={tag_id}") == [a]
    assert model_ids(client, "/models?q=bet") == [b]


def test_private_model_invisible_to_other_user(two_users):
    a, b = two_users
    mid = add_model(a, title="Secret")
    assert b.get(f"/models/{mid}").status_code == 404
    assert b.get(f"/models/{mid}/edit").status_code == 404
    token = csrf(b, "/models")
    b.post(f"/models/{mid}", data={"title": "Hacked", "csrf_token": token})
    b.post(f"/models/{mid}/delete", data={"csrf_token": token})
    assert "Secret" in a.get(f"/models/{mid}").text
    assert model_ids(b) == []


def test_delete_model(client):
    do_setup(client)
    mid = add_model(client, title="Gone")
    token = csrf(client, f"/models/{mid}")
    client.post(f"/models/{mid}/delete", data={"csrf_token": token})
    assert client.get(f"/models/{mid}").status_code == 404


def test_new_category_created_from_form(client):
    do_setup(client)
    mid = add_model(client, title="X", new_category="Garden")
    assert "Garden" in client.get(f"/models/{mid}").text
```

- [ ] **Step 3: Run** → FAIL. **Step 4: Implement** service + router + templates (list with sidebar, search box, tag chips, cards, quick-add bar, pagination; full form; detail with files/images/description/print settings/share toggle/edit/delete). Model 404s use `HTTPException(404)`. Create redirects to `/models/{id}`; validation errors redirect back to `/models/new` (or `/models/{id}/edit`) with a flash. **Step 5: Run** full suite → PASS. **Step 6: Commit** `feat(library): model list, quick-add, create/edit/detail pages`.

---

### Task 6: Files and images (upload, download, rename, delete, cover)

**Files:**
- Create: `app/routers/library_assets.py`, `tests/test_library_assets.py`
- Modify: `app/templates/library/form.html`, `library/detail.html`, `app/main.py`

**Interfaces:**
- Consumes: Task 1 `file_store`, Task 5 `get_viewable`, `attach_uploads`.
- Produces routes: `GET /models/files/{fid}`, `GET /models/images/{iid}`, `POST /models/{model_id}/files/{fid}/rename` (`filename`), `POST /models/{model_id}/files/{fid}/delete`, `POST /models/{model_id}/images/{iid}/delete`, `POST /models/{model_id}/images/{iid}/cover`. Markup: download links `href="/models/files/{fid}"`, images `src="/models/images/{iid}"`.

- [ ] **Step 1: Write failing tests**

```python
import re
from io import BytesIO

from PIL import Image

from app.database import SessionLocal
from app.models import StoredFile
from tests.helpers import add_model, add_user, csrf, do_setup, login


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (40, 20), "red").save(buf, format="PNG")
    return buf.getvalue()


def _file_ids(client, mid):
    return [int(x) for x in re.findall(r'href="/models/files/(\d+)"', client.get(f"/models/{mid}").text)]


def _image_ids(client, mid):
    return [int(x) for x in re.findall(r'src="/models/images/(\d+)"', client.get(f"/models/{mid}").text)]


def test_upload_and_download_file(client):
    do_setup(client)
    mid = add_model(client, title="Cube", files=[("files", ("cube.stl", b"solid cube", "model/stl"))])
    [fid] = _file_ids(client, mid)
    r = client.get(f"/models/files/{fid}")
    assert r.status_code == 200 and r.content == b"solid cube"
    assert "attachment" in r.headers["content-disposition"] and "cube.stl" in r.headers["content-disposition"]


def test_empty_file_part_is_ignored(client):
    do_setup(client)
    mid = add_model(client, title="Nothing", files=[("files", ("", b"", "application/octet-stream"))])
    assert _file_ids(client, mid) == []
    with SessionLocal() as db:
        assert db.query(StoredFile).count() == 0


def test_too_large_file_reported_but_model_saved(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MODEL_MAX_FILE_MB", 0)
    do_setup(client)
    token = csrf(client, "/models/new")
    r = client.post("/models", data={"title": "Big", "csrf_token": token},
                    files=[("files", ("huge.stl", b"x" * 10, "model/stl"))])
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "Big" in page and "huge.stl" in page  # flashed error names the file


def test_download_filename_is_sanitised(client):
    do_setup(client)
    mid = add_model(client, title="Evil", files=[("files", ('../../etc/pa"ss\nwd.stl', b"data", "model/stl"))])
    [fid] = _file_ids(client, mid)
    cd = client.get(f"/models/files/{fid}").headers["content-disposition"]
    assert ".." not in cd and "/" not in cd.split("filename", 1)[1] and "\n" not in cd


def test_rename_and_delete_file(client):
    do_setup(client)
    mid = add_model(client, title="Parts", files=[("files", ("a.stl", b"aaa", "model/stl"))])
    [fid] = _file_ids(client, mid)
    token = csrf(client, f"/models/{mid}/edit")
    client.post(f"/models/{mid}/files/{fid}/rename", data={"filename": "base.stl", "csrf_token": token})
    assert "base.stl" in client.get(f"/models/{mid}").text
    client.post(f"/models/{mid}/files/{fid}/delete", data={"csrf_token": token})
    assert _file_ids(client, mid) == []
    with SessionLocal() as db:
        assert db.query(StoredFile).count() == 0


def test_images_upload_cover_and_delete(client):
    do_setup(client)
    mid = add_model(client, title="Pics", files=[
        ("images", ("one.png", _png(), "image/png")),
        ("images", ("two.png", _png()[:-1] + b"\x00", "image/png")),
    ])
    ids = _image_ids(client, mid)
    assert len(ids) >= 1
    r = client.get(f"/models/images/{ids[0]}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    token = csrf(client, f"/models/{mid}/edit")
    if len(ids) > 1:
        client.post(f"/models/{mid}/images/{ids[1]}/cover", data={"csrf_token": token})
        assert _image_ids(client, mid)[0] == ids[1]
    for iid in ids:
        client.post(f"/models/{mid}/images/{iid}/delete", data={"csrf_token": token})
    assert _image_ids(client, mid) == []


def test_private_assets_hidden_from_other_user(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    mid = add_model(client, title="Mine", files=[
        ("files", ("a.stl", b"aaa", "model/stl")), ("images", ("p.png", _png(), "image/png")),
    ])
    [fid] = _file_ids(client, mid)
    [iid] = _image_ids(client, mid)
    assert client2.get(f"/models/files/{fid}").status_code == 404
    assert client2.get(f"/models/images/{iid}").status_code == 404
    token = csrf(client2, "/models")
    client2.post(f"/models/{mid}/files/{fid}/delete", data={"csrf_token": token})
    assert _file_ids(client, mid) == [fid]


def test_missing_blob_shows_missing_and_404s(client):
    do_setup(client)
    mid = add_model(client, title="Lost", files=[("files", ("a.stl", b"lost bytes", "model/stl"))])
    [fid] = _file_ids(client, mid)
    from app.services import file_store
    with SessionLocal() as db:
        file_store.blob_path(db.query(StoredFile).first().sha256).unlink()
    assert "file missing" in client.get(f"/models/{mid}").text
    assert client.get(f"/models/files/{fid}").status_code == 404
```

Note: the second image in `test_images_upload_cover_and_delete` may be unreadable; the test tolerates either one or two stored images (an unreadable image is flashed, not fatal).

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Sanitise filenames: `os.path.basename` after replacing `\\` with `/`, strip control chars and `"`, collapse whitespace, cap 255, fallback `"file"`. `FileResponse(path, media_type=..., filename=safe)`. **Step 4: Run** → PASS. **Step 5: Commit** `feat(library): model files and images`.

---

### Task 7: Sharing and copy-to-library

**Files:**
- Create: `app/templates/library/shared.html`, `tests/test_library_sharing.py`
- Modify: `app/routers/library.py`, `app/services/library.py`, `library/detail.html`

**Interfaces:**
- Consumes: Task 5 `copy_model`, `get_viewable`, `delete_model`.
- Produces routes: `POST /models/{model_id}/share` (`shared` = `"1"`/`"0"`), `GET /models/shared` (`q`, `owner`, `page`), `POST /models/{model_id}/copy` (`category_id`, `tags`) → redirects to `/models/{new_id}`.

- [ ] **Step 1: Write failing tests**

```python
import re

import pytest

from tests.helpers import add_model, add_user, csrf, do_setup, login, model_ids


@pytest.fixture()
def two_users(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    return client, client2


def _share(client, mid, on=True):
    token = csrf(client, f"/models/{mid}")
    client.post(f"/models/{mid}/share", data={"shared": "1" if on else "0", "csrf_token": token})


def _copy(client, mid, **data):
    token = csrf(client, f"/models/{mid}")
    r = client.post(f"/models/{mid}/copy", data={"csrf_token": token, **data})
    assert r.status_code == 303
    return int(re.match(r"/models/(\d+)$", r.headers["location"]).group(1))


def test_shared_model_visible_read_only(two_users):
    a, b = two_users
    mid = add_model(a, title="Clip", tags="secret-tag", new_category="A-cat",
                    files=[("files", ("clip.stl", b"clip", "model/stl"))])
    assert b.get("/models/shared").text.count(f'href="/models/{mid}"') == 0
    _share(a, mid)
    assert f'href="/models/{mid}"' in b.get("/models/shared").text
    page = b.get(f"/models/{mid}").text
    assert "Clip" in page and "secret-tag" not in page and "A-cat" not in page
    assert f"/models/{mid}/edit" not in page
    fid = re.search(r'href="/models/files/(\d+)"', page).group(1)
    assert b.get(f"/models/files/{fid}").content == b"clip"
    assert b.get(f"/models/{mid}/edit").status_code == 404


def test_copy_is_independent_and_private(two_users):
    a, b = two_users
    mid = add_model(a, title="Clip", tags="a-tag", new_category="A-cat")
    _share(a, mid)
    cid = _copy(b, mid, tags="mine")
    assert cid != mid and model_ids(b) == [cid]
    page = b.get(f"/models/{cid}").text
    assert "mine" in page and "a-tag" not in page and "A-cat" not in page
    assert "copied from" in page.lower() and "admin" in page
    assert f'href="/models/{cid}"' not in a.get("/models/shared").text  # copy starts private
    assert "In your library" in b.get("/models/shared").text


def test_copy_survives_original_delete_and_download_works(two_users):
    a, b = two_users
    mid = add_model(a, title="Hinge", files=[("files", ("hinge.stl", b"hinge", "model/stl"))])
    _share(a, mid)
    cid = _copy(b, mid)
    token = csrf(a, f"/models/{mid}")
    a.post(f"/models/{mid}/delete", data={"csrf_token": token})
    page = b.get(f"/models/{cid}").text
    assert "Hinge" in page
    fid = re.search(r'href="/models/files/(\d+)"', page).group(1)
    assert b.get(f"/models/files/{fid}").content == b"hinge"


def test_unshared_model_cannot_be_copied(two_users):
    a, b = two_users
    mid = add_model(a, title="Private")
    token = csrf(b, "/models")
    r = b.post(f"/models/{mid}/copy", data={"csrf_token": token})
    assert r.status_code == 404
    assert model_ids(b) == []


def test_unshare_hides_but_keeps_copies(two_users):
    a, b = two_users
    mid = add_model(a, title="Temp")
    _share(a, mid)
    cid = _copy(b, mid)
    _share(a, mid, on=False)
    assert b.get(f"/models/{mid}").status_code == 404
    assert b.get(f"/models/{cid}").status_code == 200


def test_shared_search(two_users):
    a, b = two_users
    m1 = add_model(a, title="Planter")
    m2 = add_model(a, title="Lamp")
    _share(a, m1)
    _share(a, m2)
    html = b.get("/models/shared?q=plant").text
    assert f'href="/models/{m1}"' in html and f'href="/models/{m2}"' not in html
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(library): share models and copy shared models into a library`.

---

### Task 8: Bulk actions

**Files:**
- Create: `tests/test_library_bulk.py`
- Modify: `app/routers/library.py`, `library/list.html`, `library/_card.html`, `app/static/custom.css`

**Interfaces:**
- Produces route: `POST /models/bulk` with `action` ∈ `move|add_tag|remove_tag|share|unshare|delete`, `ids` (repeatable int), `category_id` (move; empty = Uncategorized), `tag` (tag name for add/remove), `next` (redirect target; only accepted if it starts with `/models`).
- Markup: card checkbox `<input type="checkbox" name="ids" value="{id}" form="bulk-form">`.

- [ ] **Step 1: Write failing tests**

```python
import re

from tests.helpers import add_model, add_user, csrf, do_setup, login, model_ids


def _bulk(client, action, ids, **extra):
    token = csrf(client, "/models")
    return client.post("/models/bulk", data={"action": action, "ids": ids, "csrf_token": token, **extra})


def _cat(client, name):
    token = csrf(client, "/models/categories")
    client.post("/models/categories", data={"name": name, "csrf_token": token})
    html = client.get("/models").text
    return int(re.search(rf'href="/models\?cat=(\d+)"[^>]*>\s*{name}', html).group(1))


def test_bulk_move_and_tag(client):
    do_setup(client)
    a, b, c = (add_model(client, title=t) for t in ("A", "B", "C"))
    cat = _cat(client, "Shelf")
    _bulk(client, "move", [a, b], category_id=cat)
    assert model_ids(client, f"/models?cat={cat}") == [a, b]
    _bulk(client, "add_tag", [b, c], tag="todo")
    tag_id = int(re.search(r"tag=(\d+)", client.get("/models").text).group(1))
    assert model_ids(client, f"/models?tag={tag_id}") == [b, c]
    _bulk(client, "remove_tag", [b], tag="todo")
    assert model_ids(client, f"/models?tag={tag_id}") == [c]
    _bulk(client, "move", [a], category_id="")
    assert model_ids(client, "/models?cat=none") == [a, c]


def test_bulk_share_and_delete(client):
    do_setup(client)
    a, b = add_model(client, title="A"), add_model(client, title="B")
    _bulk(client, "share", [a, b])
    assert model_ids(client, "/models/shared") == [a, b]
    _bulk(client, "unshare", [b])
    assert model_ids(client, "/models/shared") == [a]
    _bulk(client, "delete", [a, b])
    assert model_ids(client) == []


def test_bulk_ignores_foreign_ids(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    mine = add_model(client2, title="Bob's")
    theirs = add_model(client, title="Admin's")
    _bulk(client2, "delete", [mine, theirs, 99999])
    assert model_ids(client2) == []
    assert model_ids(client) == [theirs]


def test_bulk_redirect_only_to_library(client):
    do_setup(client)
    a = add_model(client, title="A")
    r = _bulk(client, "share", [a], next="https://evil.example/")
    assert r.headers["location"].startswith("/models")
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (route + action bar with JS show/hide + delete confirm with count + select-all-on-page). **Step 4: Run** → PASS. **Step 5: Commit** `feat(library): bulk move, tag, share, and delete`.

---

### Task 9: Category and tag management pages

**Files:**
- Create: `app/routers/library_admin.py`, `app/templates/library/categories.html`, `library/tags.html`, `tests/test_library_taxonomy_pages.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: Task 3 service, Task 4 `reindex`.
- Produces routes: `GET/POST /models/categories` (`name`, `parent_id`), `POST /models/categories/{cid}` (`name`, `parent_id`), `POST /models/categories/{cid}/delete`, `GET /models/tags`, `POST /models/tags/{tid}` (`name`), `POST /models/tags/{tid}/delete`. All reindex affected models.

- [ ] **Step 1: Write failing tests**

```python
import re

from tests.helpers import add_model, add_user, csrf, do_setup, login, model_ids


def _cats(client):
    html = client.get("/models/categories").text
    return {m.group(1): int(m.group(2)) for m in re.finditer(r'name="name" value="([^"]*)" form="cat-(\d+)"', html)}


def _add_cat(client, name, parent_id=""):
    token = csrf(client, "/models/categories")
    return client.post("/models/categories", data={"name": name, "parent_id": parent_id, "csrf_token": token})


def test_create_rename_move_delete_category(client):
    do_setup(client)
    _add_cat(client, "Workshop")
    ws = _cats(client)["Workshop"]
    _add_cat(client, "Holders", ws)
    holders = _cats(client)["Holders"]
    mid = add_model(client, title="Rack", category_id=holders)
    assert model_ids(client, f"/models?cat={ws}") == [mid]  # descendant included
    token = csrf(client, "/models/categories")
    client.post(f"/models/categories/{holders}", data={"name": "Racks", "parent_id": "", "csrf_token": token})
    assert "Racks" in _cats(client)
    assert model_ids(client, f"/models?cat={ws}") == []
    assert model_ids(client, "/models?q=racks") == [mid]  # search sees new path
    client.post(f"/models/categories/{holders}/delete", data={"csrf_token": token})
    assert model_ids(client, "/models?cat=none") == [mid]


def test_cycle_move_rejected(client):
    do_setup(client)
    _add_cat(client, "A")
    a = _cats(client)["A"]
    _add_cat(client, "B", a)
    b = _cats(client)["B"]
    token = csrf(client, "/models/categories")
    client.post(f"/models/categories/{a}", data={"name": "A", "parent_id": b, "csrf_token": token})
    from app.database import SessionLocal
    from app.models import LibraryCategory
    with SessionLocal() as db:
        assert db.get(LibraryCategory, a).parent_id is None
        assert db.get(LibraryCategory, b).parent_id == a


def test_tag_rename_merge_and_delete(client):
    do_setup(client)
    m1 = add_model(client, title="One", tags="gift")
    m2 = add_model(client, title="Two", tags="present")
    html = client.get("/models/tags").text
    tags = {m.group(1): int(m.group(2)) for m in re.finditer(r'name="name" value="([^"]*)" form="tag-(\d+)"', html)}
    token = csrf(client, "/models/tags")
    client.post(f"/models/tags/{tags['present']}", data={"name": "gift", "csrf_token": token})
    html = client.get("/models/tags").text
    assert 'value="present"' not in html
    assert model_ids(client, f"/models?tag={tags['gift']}") == [m1, m2]
    client.post(f"/models/tags/{tags['gift']}/delete", data={"csrf_token": token})
    assert "gift" not in client.get(f"/models/{m1}").text


def test_other_user_cannot_touch_categories_or_tags(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    _add_cat(client, "Mine")
    cid = _cats(client)["Mine"]
    add_model(client, title="T", tags="keep")
    tid = int(re.search(r'form="tag-(\d+)"', client.get("/models/tags").text).group(1))
    token = csrf(client2, "/models/categories")
    client2.post(f"/models/categories/{cid}", data={"name": "Hacked", "parent_id": "", "csrf_token": token})
    client2.post(f"/models/categories/{cid}/delete", data={"csrf_token": token})
    client2.post(f"/models/tags/{tid}/delete", data={"csrf_token": token})
    assert "Mine" in _cats(client)
    assert 'value="keep"' in client.get("/models/tags").text
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(library): category and tag management pages`.

---

### Task 10: Integration — user deletion, admin reset, upgrade, docs

**Files:**
- Modify: `app/services/users.py`, `app/routers/admin.py`, `docker-compose.yml`, `.env.example`, `README.MD`
- Create: `tests/test_library_integration.py`

- [ ] **Step 1: Write failing tests**

```python
import sqlite3

from app.database import SessionLocal
from app.models import LibraryCategory, LibraryModel, LibraryTag, StoredFile
from tests.helpers import add_model, add_user, csrf, do_setup


def test_deleting_user_removes_library_and_unshared_blobs(client):
    do_setup(client)
    add_user(client, "bob")
    from tests.helpers import login
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, follow_redirects=False) as bob:
        login(bob, "bob", "password123")
        add_model(bob, title="Bob model", tags="t", new_category="C",
                  files=[("files", ("b.stl", b"bob bytes", "model/stl"))])
    with SessionLocal() as db:
        from app.models import User
        bob_id = db.query(User).filter_by(username="bob").one().id
    token = csrf(client, "/admin")
    client.post(f"/admin/users/{bob_id}/delete", data={"csrf_token": token})
    with SessionLocal() as db:
        assert db.query(LibraryModel).count() == 0
        assert db.query(LibraryTag).count() == 0
        assert db.query(LibraryCategory).count() == 0
        assert db.query(StoredFile).count() == 0


def test_admin_reset_clears_files_dir(client, files_dir):
    do_setup(client)
    add_model(client, title="X", files=[("files", ("x.stl", b"x bytes", "model/stl"))])
    assert any(p.is_file() for p in files_dir.rglob("*"))
    token = csrf(client, "/admin")
    client.post("/admin/reset", data={"confirm_text": "Yes, Really Reset The Database", "csrf_token": token})
    assert not any(p.is_file() for p in files_dir.rglob("*"))


def test_upgrade_adds_library_tables(tmp_path, monkeypatch):
    from app.database import engine, init_db
    with engine.begin() as conn:
        for t in ["library_model_tags", "library_model_files", "library_model_images", "library_models",
                  "library_tags", "library_categories", "stored_files"]:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {t}")
        conn.exec_driver_sql("DROP TABLE IF EXISTS library_search")
    init_db()
    from app.config import DB_PATH
    names = {r[0] for r in sqlite3.connect(DB_PATH).execute("select name from sqlite_master")}
    assert {"library_models", "stored_files", "library_search"} <= names
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**: `delete_user` deletes the user's models via `library.delete_model`, then their tag links, tags, categories. Admin reset calls `file_store.clear_all()` after `reset_db()`. Add `FILES_DIR`/`MODEL_MAX_FILE_MB` to compose, `.env.example`, README (features + config table + backup notes). **Step 4: Run** full suite → PASS. **Step 5: Commit** `feat(library): cascade user deletion, reset clears files, docs`.

---

### Task 11: Manual verification

- [ ] Run the app locally (`uvicorn app.main:app --reload`), create a user, and exercise: quick-add, full form with files/images, edit, tags, category tree, search, bulk actions, share + copy as a second user, downloads. Check mobile width.
- [ ] Fix anything found with a test first; commit.
