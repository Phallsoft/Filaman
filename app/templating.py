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
    }


templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"),
    context_processors=[_global_context],
)
