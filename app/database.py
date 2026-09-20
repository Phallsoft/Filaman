import os

from sqlalchemy import create_engine, inspect, text
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

    Base.metadata.create_all(engine)
    _migrate_schema()


def _migrate_schema():
    inspector = inspect(engine)
    if "spools" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("spools")}
        with engine.begin() as conn:
            if "image_path" not in columns:
                conn.execute(text("ALTER TABLE spools ADD COLUMN image_path VARCHAR(512)"))


def reset_db():
    from . import models  # noqa: F401

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
