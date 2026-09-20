"""One-time schema migrations that SQLAlchemy's create_all cannot express.

SQLite cannot add NOT NULL columns with foreign keys or change unique constraints
in place, so tables are rebuilt: rename old -> create new from the ORM metadata ->
copy rows -> drop old.
"""
import os
import sqlite3

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable

from .database import Base

# Tables that gain user_id, in FK-dependency order.
DATA_TABLES = ["manufacturers", "material_types", "colors", "spools"]

# Columns copied from the old table (user_id is appended as a constant).
COPY_COLUMNS = {
    "manufacturers": ["id", "name", "mfg_url"],
    "material_types": ["id", "name"],
    "colors": ["id", "name", "color_code"],
    "spools": ["id", "manufacturer_id", "material_type_id", "color_id", "sku", "weight", "image_path"],
}

# Optional one-off for upgrading a single-user instance whose only account
# (typically "admin") held the real inventory: set FILAMAN_LEGACY_OWNER to a
# username and the data moves to a NEW account with that name (same password);
# the existing account keeps the admin role. Unset (the default) leaves the
# data on the existing account. Read at call time so tests can monkeypatch it.
LEGACY_OWNER_ENV = "FILAMAN_LEGACY_OWNER"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _create_sql(table: str) -> str:
    from . import models  # noqa: F401  (ensure tables are registered)
    return str(CreateTable(Base.metadata.tables[table]).compile(dialect=sqlite_dialect.dialect()))


def add_spool_image_path(db_path: str) -> bool:
    """Add spools.image_path (pre-image-support databases). Returns True if it was added."""
    conn = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "spools" not in tables or "image_path" in _columns(conn, "spools"):
            return False
        conn.execute("ALTER TABLE spools ADD COLUMN image_path VARCHAR(512)")
        conn.commit()
        return True
    finally:
        conn.close()


def migrate_file(db_path: str) -> None:
    """Bring a SQLite file (live DB or an uploaded backup) up to the current schema."""
    add_spool_image_path(db_path)
    migrate_multi_user(db_path)


def migrate_multi_user(db_path: str) -> bool:
    """Add users.is_admin and user_id to the data tables. Returns True if the rebuild ran."""
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # manual transactions
    try:
        if "users" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            return False  # empty DB; create_all will build the final schema
        if "is_admin" not in _columns(conn, "users"):
            conn.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
        # Idempotent: promote the lowest-id user only if nobody is an admin yet.
        conn.execute(
            "UPDATE users SET is_admin = 1 WHERE id = (SELECT min(id) FROM users)"
            " AND NOT EXISTS (SELECT 1 FROM users WHERE is_admin = 1)"
        )
        if "user_id" in _columns(conn, "spools"):
            return False

        # The engine never enforces foreign keys, so an old DB may already hold
        # orphans (e.g. a spool whose manufacturer was deleted). Refuse up front
        # instead of failing the post-rebuild check on every restart.
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(
                "Database has dangling foreign-key references that must be repaired "
                f"before upgrading: {problems[:5]}"
            )

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")  # RENAME must not rewrite FKs in other tables
        legacy_owner = os.environ.get(LEGACY_OWNER_ENV, "").strip()
        conn.execute("BEGIN")
        try:
            first = conn.execute("SELECT id, hashed_password FROM users ORDER BY id LIMIT 1").fetchone()
            owner_id = first[0] if first else None
            user_count = conn.execute("SELECT count(*) FROM users").fetchone()[0]
            if legacy_owner and user_count == 1:
                has_legacy = conn.execute(
                    "SELECT 1 FROM users WHERE username = ?", (legacy_owner,)
                ).fetchone() is not None
                if not has_legacy:
                    cur = conn.execute(
                        "INSERT INTO users (username, hashed_password, is_admin) VALUES (?, ?, 0)",
                        (legacy_owner, first[1]),
                    )
                    owner_id = cur.lastrowid

            for table in DATA_TABLES:
                conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
            for table in DATA_TABLES:
                conn.execute(_create_sql(table))
            for table in DATA_TABLES:
                cols = ", ".join(COPY_COLUMNS[table])
                conn.execute(
                    f"INSERT INTO {table} ({cols}, user_id) SELECT {cols}, ? FROM {table}_old",
                    (owner_id,),
                )
            for table in reversed(DATA_TABLES):
                conn.execute(f"DROP TABLE {table}_old")

            problems = conn.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"Migration left dangling foreign keys: {problems[:5]}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return True
    finally:
        conn.close()
