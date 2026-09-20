from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import MEDIA_DIR, SECRET_KEY
from .database import SessionLocal, init_db
from .models import User
from .routers import admin, ai_import, auth_routes, lookups, spools


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Filaman", lifespan=lifespan)

PUBLIC_PATHS = {"/login", "/setup"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static"):
        return await call_next(request)

    with SessionLocal() as db:
        has_users = db.query(User.id).first() is not None

    if not has_users:
        if path != "/setup":
            return RedirectResponse("/setup", status_code=303)
        return await call_next(request)

    if path == "/setup":
        return RedirectResponse("/login", status_code=303)

    if path not in PUBLIC_PATHS and not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    return await call_next(request)


# Added after the auth middleware so SessionMiddleware is outermost
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

app.include_router(auth_routes.router)
app.include_router(spools.router)
app.include_router(lookups.router)
app.include_router(ai_import.router)
app.include_router(admin.router)
