"""Model library categories (a per-user tree) and tags."""
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import LibraryCategory, LibraryModel, LibraryTag, library_model_tags

PATH_SEP = " › "
MAX_CATEGORY_NAME = 128
MAX_TAG_NAME = 64


class TaxonomyError(ValueError):
    """User-facing category/tag failure."""


# ---- categories -------------------------------------------------------------

def user_categories(db: Session, user_id: int) -> list[LibraryCategory]:
    return db.query(LibraryCategory).filter(LibraryCategory.user_id == user_id).all()


def _children_map(categories) -> dict[int | None, list[LibraryCategory]]:
    children: dict[int | None, list[LibraryCategory]] = {}
    for cat in categories:
        children.setdefault(cat.parent_id, []).append(cat)
    for siblings in children.values():
        siblings.sort(key=lambda c: c.name.lower())
    return children


def descendant_ids(categories, root_id: int) -> set[int]:
    """root_id plus every category below it."""
    children = _children_map(categories)
    found, stack = set(), [root_id]
    while stack:
        cid = stack.pop()
        if cid in found:
            continue
        found.add(cid)
        stack.extend(c.id for c in children.get(cid, []))
    return found


def category_path(by_id: dict[int, LibraryCategory], cat_id: int | None) -> str:
    names, seen = [], set()
    while cat_id is not None and cat_id in by_id and cat_id not in seen:
        seen.add(cat_id)
        names.append(by_id[cat_id].name)
        cat_id = by_id[cat_id].parent_id
    return PATH_SEP.join(reversed(names))


def tree_rows(categories, direct_counts: dict[int | None, int]) -> list[dict]:
    """Depth-first rows: {category, depth, count (incl. descendants), path}."""
    children = _children_map(categories)
    rows: list[dict] = []

    def walk(parent_id, depth, prefix) -> int:
        total = 0
        for cat in children.get(parent_id, []):
            row = {"category": cat, "depth": depth, "path": prefix + cat.name}
            rows.append(row)
            row["count"] = direct_counts.get(cat.id, 0) + walk(cat.id, depth + 1, row["path"] + PATH_SEP)
            total += row["count"]
        return total

    walk(None, 0, "")
    return rows


def _clean_name(name: str | None, limit: int, what: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise TaxonomyError(f"{what} name cannot be empty.")
    if len(name) > limit:
        raise TaxonomyError(f"{what} name must be at most {limit} characters.")
    return name


def _check_sibling_free(db: Session, user_id: int, parent_id: int | None, name: str, exclude_id: int | None = None):
    q = db.query(LibraryCategory.id).filter(
        LibraryCategory.user_id == user_id,
        LibraryCategory.parent_id.is_(None) if parent_id is None else LibraryCategory.parent_id == parent_id,
        func.lower(LibraryCategory.name) == name.lower(),
    )
    if exclude_id is not None:
        q = q.filter(LibraryCategory.id != exclude_id)
    if q.first():
        raise TaxonomyError(f"A category named '{name}' already exists there.")


def owned_category(db: Session, user_id: int, raw) -> LibraryCategory | None:
    try:
        cat_id = int(raw)
    except (TypeError, ValueError):
        return None
    return db.query(LibraryCategory).filter(LibraryCategory.id == cat_id, LibraryCategory.user_id == user_id).first()


def create_category(db: Session, user_id: int, name: str, parent_id: int | None = None) -> LibraryCategory:
    name = _clean_name(name, MAX_CATEGORY_NAME, "Category")
    if parent_id is not None and owned_category(db, user_id, parent_id) is None:
        raise TaxonomyError("Parent category not found.")
    _check_sibling_free(db, user_id, parent_id, name)
    cat = LibraryCategory(user_id=user_id, parent_id=parent_id, name=name)
    db.add(cat)
    db.flush()
    return cat


def rename_category(db: Session, cat: LibraryCategory, name: str) -> None:
    name = _clean_name(name, MAX_CATEGORY_NAME, "Category")
    _check_sibling_free(db, cat.user_id, cat.parent_id, name, exclude_id=cat.id)
    cat.name = name
    db.flush()


def move_category(db: Session, cat: LibraryCategory, new_parent_id: int | None) -> None:
    if new_parent_id == cat.parent_id:
        return
    if new_parent_id is not None:
        if owned_category(db, cat.user_id, new_parent_id) is None:
            raise TaxonomyError("Parent category not found.")
        if new_parent_id in descendant_ids(user_categories(db, cat.user_id), cat.id):
            raise TaxonomyError("A category cannot be moved inside itself.")
    _check_sibling_free(db, cat.user_id, new_parent_id, cat.name, exclude_id=cat.id)
    cat.parent_id = new_parent_id
    db.flush()


def delete_category(db: Session, cat: LibraryCategory) -> None:
    """Models become Uncategorized; child categories move up to the deleted category's parent."""
    db.query(LibraryModel).filter(LibraryModel.category_id == cat.id).update(
        {LibraryModel.category_id: None}, synchronize_session=False
    )
    for child in db.query(LibraryCategory).filter(LibraryCategory.parent_id == cat.id).all():
        child.parent_id = cat.parent_id
        # A child whose name collides with a new sibling gets a suffix rather than blocking the delete.
        try:
            _check_sibling_free(db, cat.user_id, cat.parent_id, child.name, exclude_id=child.id)
        except TaxonomyError:
            child.name = f"{child.name} ({cat.name})"[:MAX_CATEGORY_NAME]
    db.delete(cat)
    db.flush()


def affected_model_ids_for_categories(db: Session, cat_ids) -> list[int]:
    """Models filed in any of these categories or their descendants."""
    cat_ids = list(cat_ids)
    if not cat_ids:
        return []
    owner = db.query(LibraryCategory.user_id).filter(LibraryCategory.id == cat_ids[0]).scalar()
    if owner is None:
        return []
    cats = user_categories(db, owner)
    all_ids: set[int] = set()
    for cid in cat_ids:
        all_ids |= descendant_ids(cats, cid)
    return [m for (m,) in db.query(LibraryModel.id).filter(LibraryModel.category_id.in_(all_ids))]


# ---- tags -------------------------------------------------------------------

def parse_tag_names(text: str | None) -> list[str]:
    names, seen = [], set()
    for raw in (text or "").split(","):
        name = " ".join(raw.split())[:MAX_TAG_NAME]
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def get_or_create_tags(db: Session, user_id: int, names) -> list[LibraryTag]:
    tags = []
    for name in names:
        tag = (
            db.query(LibraryTag)
            .filter(LibraryTag.user_id == user_id, func.lower(LibraryTag.name) == name.lower())
            .first()
        )
        if tag is None:
            tag = LibraryTag(user_id=user_id, name=name)
            db.add(tag)
            db.flush()
        tags.append(tag)
    return tags


def tag_model_ids(db: Session, tag_id: int) -> list[int]:
    return [m for (m,) in db.query(library_model_tags.c.model_id).filter(library_model_tags.c.tag_id == tag_id)]


def rename_tag(db: Session, tag: LibraryTag, new_name: str) -> LibraryTag:
    """Rename; if another tag already has that name, merge into it. Returns the surviving tag."""
    new_name = _clean_name(new_name, MAX_TAG_NAME, "Tag")
    other = (
        db.query(LibraryTag)
        .filter(LibraryTag.user_id == tag.user_id, func.lower(LibraryTag.name) == new_name.lower(), LibraryTag.id != tag.id)
        .first()
    )
    if other is None:
        tag.name = new_name
        db.flush()
        return tag
    already = set(tag_model_ids(db, other.id))
    for model_id in tag_model_ids(db, tag.id):
        if model_id not in already:
            db.execute(library_model_tags.insert().values(model_id=model_id, tag_id=other.id))
    delete_tag(db, tag)
    return other


def delete_tag(db: Session, tag: LibraryTag) -> None:
    db.execute(library_model_tags.delete().where(library_model_tags.c.tag_id == tag.id))
    db.delete(tag)
    db.flush()
