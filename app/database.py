import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DB_PATH


class Base(DeclarativeBase):
    pass


os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401
    from .migrations import add_spool_image_path, migrate_multi_user

    Base.metadata.create_all(engine)
    changed = add_spool_image_path(DB_PATH)
    changed = migrate_multi_user(DB_PATH) or changed
    if changed:
        engine.dispose()  # pooled connections saw the old schema


def reset_db():
    from . import models  # noqa: F401

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def get_owned(db, model, item_id: int, user_id: int):
    """Fetch a user-owned row by id, or None if it doesn't exist or belongs to someone else."""
    return db.query(model).filter(model.id == item_id, model.user_id == user_id).first()
