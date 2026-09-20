import os
import secrets
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader for local dev (docker compose loads .env itself)."""
    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY") or ""
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print("WARNING: SECRET_KEY not set; generated a random one (sessions reset on restart).")

DB_PATH = os.environ.get("DB_PATH", "./data/filaman.db")
MEDIA_DIR = os.environ.get(
    "MEDIA_DIR",
    str(Path(DB_PATH).expanduser().resolve().parent / "images"),
)

AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "openai/gpt-4o-mini")

# Optional Bright Data MCP server (https://mcp.brightdata.com/mcp?token=...) used to
# fetch product pages that block direct scraping (e.g. Amazon).
BRIGHTDATA_MCP_URL = os.environ.get("BRIGHTDATA_MCP_URL", "")
