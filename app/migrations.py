"""One-time schema migrations that SQLAlchemy's create_all cannot express.

SQLite cannot add NOT NULL columns with foreign keys or change unique constraints
in place, so tables are rebuilt: rename old -> create new from the ORM metadata ->
copy rows -> drop old.
"""
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

# One-off for the original single-user instance: its only account was named
# "admin" but held the real inventory. The data moves to a new "jpaul" account
# (same password) and "admin" keeps the admin role. Safe to delete once run.
LEGACY_DATA_OWNER = "jpaul"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _create_sql(table: str) -> str:
    from . import models  # noqa: F401  (ensure tables are registered)
    return str(CreateTable(Base.metadata.tables[table]).compile(dialect=sqlite_dialect.dialect()))


def migrate_multi_user(db_path: str) -> bool:
    """Add users.is_admin and user_id to the data tables. Returns True if the rebuild ran."""
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # manual transactions
    try:
        if "users" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            return False  # empty DB; create_all will build the final schema
        if "is_admin" not in _columns(conn, "users"):
            conn.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = (SELECT min(id) FROM users)")
        if "user_id" in _columns(conn, "spools"):
            return False

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")  # RENAME must not rewrite FKs in other tables
        conn.execute("BEGIN")
        try:
            first = conn.execute("SELECT id, hashed_password FROM users ORDER BY id LIMIT 1").fetchone()
            owner_id = first[0] if first else None
            user_count = conn.execute("SELECT count(*) FROM users").fetchone()[0]
            has_legacy = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (LEGACY_DATA_OWNER,)
            ).fetchone() is not None
            if user_count == 1 and not has_legacy:
                cur = conn.execute(
                    "INSERT INTO users (username, hashed_password, is_admin) VALUES (?, ?, 0)",
                    (LEGACY_DATA_OWNER, first[1]),
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
