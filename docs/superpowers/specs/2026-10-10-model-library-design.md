# Model library — design

Date: 2026-10-10
Branch: `feature/model-library`

## Goal

Add a 3D model library to Filaman: a per-user collection of models that can be
as light as "a title and a link" or as rich as uploaded files, images, print
settings and notes, organised with a category tree and tags, searchable, and
optionally shared read-only with every other user.

This is sub-project 1 of 5. Later sub-projects, each with its own spec:

2. URL import wizard (AI extraction, pick-what-to-import, Firecrawl fallback)
3. Browser extension (rendered-page capture, authenticated file download, API tokens)
4. In-app 3D viewer (three.js)
5. Material / print-time estimation

Nothing here builds 2–5; `source_url` and `platform` are the only hooks they need.

## Decisions

- Models are **standalone**: no link to spools or materials. Print settings are free text.
- Models are **private by default**, with a per-model "share with all users" toggle.
  Shared models are read-only to others.
- Shared models carry **no category or tags** for other users. "Add to my library"
  makes an independent **copy** owned by the adder.
- Files are **content-addressed** (SHA-256) and stored once, so copying a shared
  model costs no extra disk.
- **One category per model** (nullable = Uncategorized) **plus any number of tags**.
- Only `title` is required.
- Description and print settings are **plain text** (newlines preserved). Markdown is deferred.
- Tags are **not** copied with a shared model.
- Same app, same stack (FastAPI + Jinja + htmx + Pico), no build step.

Implementation-time decisions (made during autonomous development, listed for review):

- ORM/table names are prefixed `library_` (`LibraryModel` → `library_models`,
  `library_categories`, `library_tags`, …) to avoid colliding with the `app.models`
  module name and generic words like `categories`.
- Model images are normalized (EXIF-rotated, longest side ≤ 1600 px, JPEG q88) without
  the spool crop editor; the spool editor is single-image and square. Cards use CSS
  `object-fit: cover`.
- The library list paginates at 60 models per page.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `FILES_DIR` | next to `DB_PATH` in `files/` (`/data/files` in the container) | Content-addressed model file and model image store. Not web-mounted. |
| `MODEL_MAX_FILE_MB` | `250` | Per-file upload limit for model files. |

## Data model

```
library_categories   id, user_id → users, parent_id → library_categories (nullable), name
                     unique (user_id, parent_id, name)

library_tags         id, user_id → users, name                unique (user_id, name)

library_models       id, user_id → users
                     title           String(255) NOT NULL
                     source_url      String(2048) nullable
                     platform        String(64)   nullable
                     author          String(255)  nullable
                     license         String(128)  nullable
                     description     Text         nullable
                     print_settings  Text         nullable
                     category_id     → library_categories, nullable
                     is_shared       Boolean NOT NULL default false
                     copied_from_id  → library_models, nullable (cleared when original deleted)
                     created_at, updated_at  DateTime (UTC)

library_model_tags   model_id → library_models, tag_id → library_tags   PK (model_id, tag_id)

stored_files         id, sha256 String(64) unique, size Integer, content_type String(128)
                     blob at FILES_DIR/<sha256[:2]>/<sha256>

library_model_files  id, model_id, stored_file_id, filename String(255), position Integer
library_model_images id, model_id, stored_file_id, position Integer   (lowest position = cover)
```

SQLite unique constraints treat NULLs as distinct, so `(user_id, parent_id, name)`
does not stop two top-level categories with the same name; the service layer
checks sibling-name uniqueness explicitly (case-insensitive).

### Rules

- **Blob lifetime.** No reference counter. After any delete of a model, file or image,
  `release(stored_file_ids)` deletes each `stored_files` row (and its blob) that no
  `library_model_files` or `library_model_images` row references any more.
- **Copy.** Copies title, source_url, platform, author, license, description,
  print_settings; new file/image rows pointing at the same stored files. Not copied:
  category, tags, `is_shared`. Sets `copied_from_id`. The adder chooses category
  and tags in the copy dialog.
- **Unshare / delete original.** Copies are unaffected; deleting clears `copied_from_id`
  on copies.
- **Delete category.** Its models become Uncategorized; its children move up to its parent.
- **Move category.** Rejected if the new parent is the category itself or one of its descendants.
- **Delete user.** Deletes their models (releasing blobs), tags and categories, inside
  the existing `services.users.delete_user`.

## Storage service (`app/services/file_store.py`)

- `store_upload(upload, max_bytes) -> StoredFile`: stream the upload in 1 MiB chunks to
  `FILES_DIR/tmp/<uuid>`, hashing as it goes; abort and delete the temp file past
  `max_bytes` (`FileTooLarge`). If the hash already exists, drop the temp file and
  return the existing row; otherwise `os.replace` into place and insert.
- `store_bytes(data, content_type) -> StoredFile`: same for in-memory data (normalized images).
- `blob_path(stored) -> Path`, `exists(stored) -> bool`.
- `release(db, ids)`: the garbage collection rule above.
- `clear_all()`: delete everything under `FILES_DIR` (admin reset).

Blob paths are derived only from the hash; user-supplied filenames are never used on disk.

## Search (`app/services/library_search.py`)

FTS5 virtual table `library_search(model_id UNINDEXED, title, author, platform,
description, print_settings, tags, category_path)`, tokenizer `unicode61 remove_diacritics 2`.

- Maintained by application code: `reindex(db, model_ids)` deletes and reinserts rows.
  Called after model create/update/copy, tag add/remove/rename/merge (affected models),
  category rename/move/delete (models in the subtree) and bulk actions.
- `ensure_index(engine)` creates the table if missing and rebuilds it if its row count
  differs from `library_models`; called from `init_db()` and `reset_db()`.
- Query building: user input is split into words, each word quoted as an FTS5 string
  with a trailing `*` (prefix match) and AND-ed, so FTS syntax in user input cannot
  raise errors.
- Results are intersected with the normal SQL filters (owner or shared, category subtree, tags).

## Pages and routes (`app/routers/library.py`)

All under `/models`; nav gets a **Models** link.

| Route | Purpose |
|---|---|
| `GET /models` | Library: category sidebar, search `q`, `cat` (id / `none`), `tag` (repeatable, AND), `page` |
| `POST /models/quick` | Quick-add: `url`, `title`, `category_id` |
| `GET /models/new`, `POST /models` | Full form create |
| `GET /models/{id}` | Detail (owner or shared) |
| `GET /models/{id}/edit`, `POST /models/{id}` | Edit (owner) |
| `POST /models/{id}/delete` | Delete (owner) |
| `POST /models/{id}/share` | Set `is_shared` from form value |
| `POST /models/{id}/files`, `POST /models/{id}/files/{fid}/delete`, `POST /models/{id}/files/{fid}/rename` | File management |
| `POST /models/{id}/images`, `POST /models/{id}/images/{iid}/delete`, `POST /models/{id}/images/{iid}/cover` | Image management |
| `GET /models/files/{fid}` | Download (attachment, original filename) |
| `GET /models/images/{iid}` | Image bytes (inline) |
| `GET /models/shared` | Shared library: `q`, `owner`, `page` |
| `POST /models/{id}/copy` | Add a shared model to my library (`category_id`, `tags`) |
| `POST /models/bulk` | `action` ∈ move/add_tag/remove_tag/share/unshare/delete, `ids` (repeatable) |
| `GET /models/categories` + POSTs | Category tree management |
| `GET /models/tags` + POSTs | Tag management (rename merges on collision) |

### Library view

- Sidebar tree with "All" and "Uncategorized"; counts include descendants. On narrow
  screens it collapses into a `<details>` block above the list.
- Selecting a category includes its descendants.
- Cards: cover image or placeholder, title, platform badge, author, tags, file count,
  shared icon, select checkbox.
- Quick-add bar: URL + title + Add, into the current category. Blank title is derived
  from the URL (below). If nothing can be derived, the user is sent back with an error.
- Bulk bar appears when any checkbox is checked: Move to category, Add tag, Remove tag,
  Share, Unshare, Delete (JS confirm with count). Server ignores IDs the user does not own.

### Title from URL (`app/services/library_urls.py`)

No network. Take the last meaningful path segment, strip a leading numeric id and
separators (`123-wera-bit-holder` → `wera bit holder`), drop common file extensions,
replace `-`/`_`/`+`/`%20` with spaces, collapse whitespace, capitalise the first letter.
Skip segments that are purely numeric or generic (`model`, `models`, `thing`, `things`,
`files`, `download`, `en`, `de`, …). Thingiverse `thing:123` yields nothing (user must
type a title).

`platform_for(url)`: known hosts map to display names (printables.com → Printables,
thingiverse.com → Thingiverse, makerworld.com → MakerWorld, cults3d.com → Cults,
myminifactory.com → MyMiniFactory, thangs.com → Thangs, grabcad.com → GrabCAD,
github.com → GitHub); otherwise the host without `www.`. Filled automatically when
the platform field is left blank.

### Shared library

Lists every `is_shared` model (including the viewer's own, marked "yours"), with search
and an owner filter. Detail page is read-only with downloads. "Add to my library" opens
a `<dialog>` with category and tags; models the viewer already copied show
"In your library" linking to the copy.

## Access control

- Owned rows: `get_owned(db, Model, id, user.id)` as everywhere else.
- `get_viewable(db, model_id, user)`: owner, or any logged-in user if `is_shared`.
  Used by detail, file download, image, and copy.
- Every POST verifies CSRF.
- Downloads: `FileResponse` with `Content-Disposition: attachment` and a sanitised
  filename; images inline with their stored content type.

## Errors and edge cases

- Empty title with no URL-derived fallback: flash error, back to the form with input preserved.
- `source_url` must be `http(s)` if given; otherwise flash error.
- Upload over the limit: other changes still saved; flash names the failing file.
- Stored file row whose blob is missing: file list shows "file missing"; download 404.
- Admin reset clears `FILES_DIR`. Admin restore does not touch it, and nothing deletes
  unreferenced blobs at startup.

## Testing

pytest, existing per-test SQLite harness plus a `FILES_DIR` temp dir.

- Scoping: user B gets 404 / no effect on A's private model, files, images, bulk actions.
- Sharing: B views/downloads a shared model, cannot edit; copy has no category, tags,
  or shared flag; unshare/delete of original leaves the copy.
- File store: duplicate upload stored once; last-reference delete removes the blob;
  one-of-two delete keeps it; size limit enforced and temp file removed.
- Categories: subtree counts; cycle move rejected; delete re-homes models and children.
- Search: matches tag names and category path; category rename updates results;
  FTS syntax in input does not error.
- Bulk: mixed owned/foreign IDs only affect owned ones.
- Title from URL: Printables, Thingiverse, MakerWorld, Cults and junk URLs.
- Upgrade: a database without the library tables gains them and the FTS index on
  `init_db()`; spools untouched.
