"""FTS5 index over the model library.

Rows include tag names and the category path, which live in other tables, so
the index is maintained by application code (reindex) rather than triggers.
"""
import re
from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..models import LibraryModel
from .library_taxonomy import category_path, user_categories

TABLE = "library_search"
CREATE_SQL = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE} USING fts5("
    "model_id UNINDEXED, title, author, platform, description, print_settings, tags, category_path, "
    "tokenize = 'unicode61 remove_diacritics 2')"
)


def ensure_index(engine: Engine) -> None:
    """Create the index if missing and rebuild it if it is out of step with library_models."""
    with engine.begin() as conn:
        conn.exec_driver_sql(CREATE_SQL)
        indexed = conn.exec_driver_sql(f"SELECT count(*) FROM {TABLE}").scalar()
        models = conn.exec_driver_sql("SELECT count(*) FROM library_models").scalar()
    if indexed != models:
        with Session(bind=engine) as db:
            db.execute(text(f"DELETE FROM {TABLE}"))
            reindex(db, [mid for (mid,) in db.query(LibraryModel.id)])
            db.commit()


def drop_index(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {TABLE}")


def reindex(db: Session, model_ids: Iterable[int]) -> None:
    """Refresh the index rows for these models (deleted models just lose their row)."""
    ids = sorted(set(model_ids))
    if not ids:
        return
    db.flush()
    cats_by_user: dict[int, dict] = {}
    for mid in ids:
        db.execute(text(f"DELETE FROM {TABLE} WHERE model_id = :id"), {"id": mid})
        model = db.get(LibraryModel, mid)
        if model is None:
            continue
        db.refresh(model, ["tags"])
        if model.user_id not in cats_by_user:
            cats_by_user[model.user_id] = {c.id: c for c in user_categories(db, model.user_id)}
        db.execute(
            text(
                f"INSERT INTO {TABLE} (model_id, title, author, platform, description, print_settings, tags, category_path) "
                "VALUES (:id, :title, :author, :platform, :description, :print_settings, :tags, :category_path)"
            ),
            {
                "id": model.id,
                "title": model.title,
                "author": model.author or "",
                "platform": model.platform or "",
                "description": model.description or "",
                "print_settings": model.print_settings or "",
                "tags": " ".join(t.name for t in model.tags),
                "category_path": category_path(cats_by_user[model.user_id], model.category_id),
            },
        )


# What other users can see of a shared model: never the owner's tags or category path.
PUBLIC_COLUMNS = ("title", "author", "platform", "description", "print_settings")


def fts_query(q: str | None, columns: tuple[str, ...] | None = None) -> str | None:
    """Turn free text into a safe FTS5 query: every word quoted, prefix-matched, AND-ed,
    optionally restricted to some columns."""
    words = re.findall(r"\w+", q or "")
    if not words:
        return None
    match = " AND ".join(f'"{w}"*' for w in words)
    return f"{{{' '.join(columns)}}} : ({match})" if columns else match


def search_ids(db: Session, q: str | None, columns: tuple[str, ...] | None = None) -> set[int] | None:
    """Model ids matching q, or None when q has no searchable words (no filter)."""
    match = fts_query(q, columns)
    if match is None:
        return None
    rows = db.execute(text(f"SELECT model_id FROM {TABLE} WHERE {TABLE} MATCH :q"), {"q": match})
    return {int(r[0]) for r in rows}
