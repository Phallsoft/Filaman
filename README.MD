# Filaman: The Best 3D Printer Filament Spool Manager

A simple, frictionless, self-hosted manager for your 3D printer filament spool inventory.
Built for people who found Spoolman overkill — no per-spool records, no real-time consumption
tracking, just "I have 3 black PLA spools, 1kg each."

## Features

- **Quantity-based inventory** — one record per spool type with a qty (and one-tap ± adjusters)
- **Spool images** — upload an image, use an image URL, crop/position it, or choose images detected during AI import
- **Quick-create everywhere** — add a manufacturer, material, or color inline without leaving the spool form
- **AI import** — paste a product page URL or product text; the configured LLM extracts manufacturer,
  material, color (with hex code), SKU, weight, and pack quantity for you to review, edit, and save.
    Product page images are detected from metadata such as `og:image` so you can choose one during review.
  Missing manufacturers/materials/colors are created automatically.
- **Smart merging** — adding a spool identical to an existing one just bumps the quantity
- **Clean, mobile-friendly UI** with light/dark theme
- **Form-based auth** with first-run account setup
- **Admin tools** — change password, one-click database backup/restore, and a (deliberately scary) database reset

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
| `MEDIA_DIR` | next to `DB_PATH` in `images/` | Local directory for stored spool images |

The database lives in the `filaman-data` Docker volume. Backups taken from the Admin page are
plain SQLite files and can be restored from the same page.

## Local development

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
.venv/Scripts/python -m uvicorn app.main:app --reload
```

`.env` in the project root is read automatically. Run `python smoke_admin.py` (with the server
running and an `admin`/`testpass123` account) to exercise the backup/reset/restore flows.

## Tech stack

FastAPI · SQLAlchemy · SQLite · Jinja2 · htmx · Pico.css — single container, no external services.