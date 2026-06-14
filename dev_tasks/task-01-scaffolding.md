# Task 01 — Project scaffolding & Docker

## Goal
Runnable FastAPI skeleton, deployable via docker compose.

## Deliverables
- `requirements.txt`: fastapi, uvicorn[standard], jinja2, sqlalchemy, bcrypt, httpx, python-multipart, itsdangerous
- `app/main.py`: FastAPI app, session middleware, auth middleware, static mount, router includes
- `app/config.py`: env-driven settings — `SECRET_KEY`, `DB_PATH`, `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL`
- `Dockerfile`: python:3.12-slim, non-root user, uvicorn entrypoint
- `docker-compose.yml`: single service, `/data` volume for SQLite, env vars passed through (reads `.env`)
- `.env.example` + stub `.env` (gitignored) for local secrets (OpenRouter key)
- `.gitignore`, `.dockerignore`

## Notes
- AI provider is any OpenAI-compatible endpoint; defaults target OpenRouter.
- SECRET_KEY auto-generates (with warning) if unset so first run is frictionless.
