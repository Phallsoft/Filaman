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
