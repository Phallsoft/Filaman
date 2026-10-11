from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import config
from .auth import get_csrf_token, pop_flashes


def _global_context(request: Request) -> dict:
    return {
        "csrf_token": get_csrf_token(request),
        "flashes": pop_flashes(request),
        "ai_enabled": bool(config.AI_API_KEY),
        "username": request.session.get("username"),
        "is_admin": bool(request.session.get("is_admin")),
    }


templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"),
    context_processors=[_global_context],
)


def _filesize(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"


templates.env.filters["filesize"] = _filesize
