import os
import sqlite3
import tempfile

from ..config import DB_PATH
from ..database import engine

REQUIRED_TABLES = {
    "users",
    "manufacturers",
    "material_types",
    "colors",
    "spools",
    "spool_inventory",
}


def create_backup() -> str:
    """Snapshot the live database to a temp file; returns its path."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return tmp_path


def validate_backup(path: str) -> str | None:
    """Returns an error message, or None if the file is a valid Filaman database."""
    try:
        with open(path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return "File is not a SQLite database."
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        finally:
            conn.close()
        tables = {r[0] for r in rows}
        missing = REQUIRED_TABLES - tables
        if missing:
            return f"Database is missing expected tables: {', '.join(sorted(missing))}."
        return None
    except sqlite3.Error as e:
        return f"Could not read database file: {e}"


def restore_backup(path: str) -> None:
    """Replace the live database with the validated file at path."""
    engine.dispose()
    os.replace(path, DB_PATH)
