from sqlalchemy.orm import Session

from ..auth import hash_password
from ..models import User

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
    """Delete the account. (Task 4 extends this to remove the user's data and images.)"""
    db.delete(user)
    db.flush()
