# Filaman: The Best 3D Printer Filament Spool Manager

A simple, frictionless, self-hosted manager for your 3D printer filament spool inventory.
Built for people who found Spoolman overkill — no per-spool records, no real-time consumption
tracking, just "I have 3 black PLA spools, 1kg each."

## Features

- **Quantity-based inventory** — one record per spool type with a qty (and one-tap ± adjusters)
- **Spool images** — upload an image, use an image URL, crop/position it, or choose images detected during AI import
- **Multiple users** — each account has its own spools and lookup lists; an admin creates accounts and handles backups
- **Quick-create everywhere** — add a manufacturer, material, or color inline without leaving the spool form
- **AI import** — paste a product page URL or product text; the configured LLM extracts manufacturer,
  material, color (with hex code), SKU, weight, and pack quantity for you to review, edit, and save.
    Product page images are detected from metadata such as `og:image` so you can choose one during review.
  Missing manufacturers/materials/colors are created automatically.
- **Smart merging** — adding a spool identical to an existing one just bumps the quantity
- **Clean, mobile-friendly UI** with light/dark theme
- **Form-based auth** with first-run account setup
- **Admin tools** — manage user accounts, one-click database backup/restore, and a (deliberately scary) database reset; every user changes their own password under Settings

## Quickstart

```bash
cp .env.example .env   # then edit .env (see below)
docker compose up -d
```

Open http://localhost:8000 — you'll be prompted to create your account on first load.

## Configuration (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | random per start | Session signing key. Set one so logins survive restarts: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `AI_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible API base URL (OpenRouter, OpenAI, Ollama, …) |
| `AI_API_KEY` | _(empty)_ | API key for the AI import feature. Leave empty to disable AI import. |
| `AI_MODEL` | `openai/gpt-4o-mini` | Model name to use for extraction |
| `BRIGHTDATA_MCP_URL` | _(empty)_ | Optional [Bright Data MCP](https://brightdata.com) endpoint (`https://mcp.brightdata.com/mcp?token=...`). Used as a fallback when a direct page fetch fails or is blocked by bot protection (e.g. Amazon). |
| `DB_PATH` | `/data/filaman.db` (container) | SQLite database file location |
| `FILAMAN_PORT` | `8000` | Host port to publish the app on |
| `FILAMAN_LEGACY_OWNER` | _(empty)_ | Optional, one-time: when upgrading a single-user database, move the existing inventory to a **new** user with this name (same password as the existing account); the existing account keeps the admin role. Leave empty to keep the data on the existing account. |
| `MEDIA_DIR` | next to `DB_PATH` in `images/` | Local directory for stored spool images |

The database and spool images live in the `filaman-data` Docker volume. The first account created
at `/setup` is the admin; it adds other users from the Admin page (Settings → change password is
available to everyone). Backups taken from the Admin page are plain SQLite files (no images) and
can be restored from the same page. Upgrading an existing single-user database migrates it
automatically at startup; the existing account keeps its inventory unless `FILAMAN_LEGACY_OWNER`
names a new user to move it to. After upgrading, log out and back in so the Admin link appears
(sessions from before the upgrade do not carry the admin flag).

## Backup, restore, and moving to another host

All persistent data is in the `filaman-data` volume (`/data` in the container): `filaman.db` and
`images/spools/*.jpg`. The scripts below archive the whole volume; they stop the container briefly
so the SQLite file is copied consistently. They run on Linux and in Git Bash on Windows.

```bash
scripts/backup.sh                 # -> backups/filaman-data-YYYYmmdd-HHMMSS.tgz
scripts/backup.sh path/to/out.tgz # custom location
scripts/restore.sh backups/filaman-data-20260101-120000.tgz   # replaces current data
```

To move to a new server:

```bash
# old host
scripts/backup.sh
scp backups/filaman-data-*.tgz .env user@server:~/filaman/

# new host
git clone https://github.com/Phallsoft/Filaman.git filaman && cd filaman
scripts/restore.sh filaman-data-*.tgz   # creates the volume if needed
docker compose up -d --build
```

Copy `.env` too: keeping the same `SECRET_KEY` preserves existing logins. `backups/` is gitignored.

## Local development

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
.venv/Scripts/python -m uvicorn app.main:app --reload
pip install -r requirements-dev.txt
python -m pytest -q
```

`.env` in the project root is read automatically. Run `python smoke_admin.py` (with the server
running and an `admin`/`testpass123` account) to exercise the backup/reset/restore flows.

## Tech stack

FastAPI · SQLAlchemy · SQLite · Jinja2 · htmx · Pico.css — single container, no external services.