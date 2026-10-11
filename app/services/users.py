from sqlalchemy.orm import Session

from ..auth import hash_password
from ..models import Color, LibraryCategory, LibraryModel, LibraryTag, Manufacturer, MaterialType, Spool, User
from .images import delete_image
from .library import delete_model
from .library_taxonomy import delete_tag

MIN_PASSWORD = 8


def validate_password(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    return None


def create_user(db: Session, username: str, password: str, *, is_admin: bool = False) -> User:
    """Create a user or raise ValueError with a message suitable for a flash."""
    username = (username or "").strip()
    if not username or len(username) > 64:
        raise ValueError("Username must be 1–64 characters.")
    err = validate_password(password)
    if err:
        raise ValueError(err)
    if db.query(User.id).filter(User.username == username).first() is not None:
        raise ValueError(f"Username '{username}' already exists.")
    user = User(username=username, hashed_password=hash_password(password), is_admin=is_admin)
    db.add(user)
    db.flush()
    return user


def delete_user(db: Session, user: User) -> None:
    """Delete the account and everything it owns: spools (+inventory, +image files), lookups,
    and the model library (models, unreferenced blobs, tags, categories)."""
    for spool in db.query(Spool).filter(Spool.user_id == user.id).all():
        delete_image(spool.image_path)
        db.delete(spool)  # inventory row cascades via the relationship
    db.flush()
    for model in db.query(LibraryModel).filter(LibraryModel.user_id == user.id).all():
        delete_model(db, model)
    for tag in db.query(LibraryTag).filter(LibraryTag.user_id == user.id).all():
        delete_tag(db, tag)
    # Detach parents first so delete order cannot trip the self-referencing FK.
    cats = db.query(LibraryCategory).filter(LibraryCategory.user_id == user.id).all()
    for cat in cats:
        cat.parent_id = None
    db.flush()
    for cat in cats:
        db.delete(cat)
    db.flush()
    for model in (Manufacturer, MaterialType, Color):
        db.query(model).filter(model.user_id == user.id).delete(synchronize_session=False)
    db.delete(user)
    db.flush()
