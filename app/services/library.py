"""Model library rules: validation, uploads, copying, deletion."""
import re
from dataclasses import dataclass, field

from fastapi import UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config
from ..models import LibraryCategory, LibraryModel, LibraryModelFile, LibraryModelImage, User
from . import file_store
from .images import MAX_IMAGE_BYTES, ImageError, fetch_remote_image, normalize_photo
from .library_search import reindex
from .library_taxonomy import TaxonomyError, create_category, get_or_create_tags, owned_category
from .library_urls import is_http_url, platform_for, title_from_url

FIELD_LIMITS = {"platform": 64, "author": 255, "license": 128}


def get_viewable(db: Session, model_id: int, user: User) -> LibraryModel | None:
    """The model if the user owns it or it is shared; None otherwise."""
    model = db.get(LibraryModel, model_id)
    if model and (model.user_id == user.id or model.is_shared):
        return model
    return None


def _clean(value: str | None, limit: int | None = None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    return value[:limit] if limit else value


def apply_fields(model: LibraryModel, *, title, source_url, platform, author, license, description, print_settings) -> str | None:
    """Validate and assign the editable fields. Returns an error message and leaves the model untouched on failure."""
    url = _clean(source_url)
    if url and not is_http_url(url):
        return "The URL must start with http:// or https://."
    if url and len(url) > 2048:
        return "The URL is too long."
    title = " ".join((title or "").split()) or title_from_url(url)
    if not title:
        return "A title is required (it couldn't be worked out from the URL)."
    model.title = title[:255]
    model.source_url = url
    model.platform = _clean(platform, FIELD_LIMITS["platform"]) or platform_for(url)
    model.author = _clean(author, FIELD_LIMITS["author"])
    model.license = _clean(license, FIELD_LIMITS["license"])
    model.description = _clean(description)
    model.print_settings = _clean(print_settings)
    return None


def resolve_category(db: Session, user_id: int, category_id, new_category: str | None) -> tuple[int | None, str | None]:
    """Pick the category for a model: an owned category, optionally with a new subcategory created under it.

    Returns (category_id, error). Foreign or unknown ids fall back to Uncategorized.
    """
    parent = owned_category(db, user_id, category_id)
    name = " ".join((new_category or "").split())
    if not name:
        return (parent.id if parent else None), None
    try:
        return create_category(db, user_id, name, parent.id if parent else None).id, None
    except TaxonomyError as e:
        existing = (
            db.query(LibraryCategory)
            .filter(
                LibraryCategory.user_id == user_id,
                LibraryCategory.parent_id.is_(None) if parent is None else LibraryCategory.parent_id == parent.id,
                func.lower(LibraryCategory.name) == name.lower(),
            )
            .first()
        )
        if existing:
            return existing.id, None
        return (parent.id if parent else None), str(e)


def set_tags(db: Session, model: LibraryModel, names: list[str]) -> None:
    model.tags = get_or_create_tags(db, model.user_id, names)


def safe_filename(name: str | None) -> str:
    """A display/download name: no directories, quotes, or control characters."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f\x7f"]', "", name)
    name = " ".join(name.split()).strip(". ")
    return name[:255] or "file"


def _next_position(items) -> int:
    return max((i.position for i in items), default=-1) + 1


@dataclass
class StagedUploads:
    """Uploads copied into the file store's tmp area, ready to attach; nothing in the database yet."""

    files: list[tuple[str, file_store.Staged]] = field(default_factory=list)
    images: list[file_store.Staged] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def discard(self) -> None:
        file_store.discard([s for _, s in self.files] + self.images)


def stage_uploads(files: list[UploadFile] | None, images: list[UploadFile] | None, image_url: str | None = None) -> StagedUploads:
    """Copy, hash, fetch and normalize uploads with no database access (slow work happens here).

    Synchronous: call it from a worker thread. Skipped items become user-facing errors.
    """
    staged = StagedUploads()
    max_mb = config.MODEL_MAX_FILE_MB
    for upload in files or []:
        if not upload or not upload.filename:
            continue
        name = safe_filename(upload.filename)
        content_type = file_store.guess_type(upload.filename, upload.content_type)
        try:
            staged.files.append((name, file_store.stage_file(upload.file, name, content_type, max_mb * 1024 * 1024)))
        except file_store.FileTooLarge:
            staged.errors.append(f"{name} is larger than {max_mb} MB and was not saved.")

    raw_images: list[tuple[str, bytes]] = []
    for upload in images or []:
        if not upload or not upload.filename:
            continue
        raw = upload.file.read(MAX_IMAGE_BYTES + 1)
        if len(raw) > MAX_IMAGE_BYTES:
            staged.errors.append(f"{safe_filename(upload.filename)} is over 8 MB and was not saved.")
            continue
        raw_images.append((safe_filename(upload.filename), raw))
    url = _clean(image_url)
    if url:
        try:
            raw, _ = fetch_remote_image(url)
            raw_images.append((url, raw))
        except ImageError as e:
            staged.errors.append(f"Image URL: {e}")
    for label, raw in raw_images:
        try:
            staged.images.append(file_store.stage_bytes(normalize_photo(raw), "image/jpeg"))
        except ImageError as e:
            staged.errors.append(f"{label}: {e}")
    return staged


def attach_staged(db: Session, model: LibraryModel, staged: StagedUploads) -> None:
    """File staged uploads and attach them to the model (fast; runs inside the request's transaction)."""
    for name, s in staged.files:
        stored = file_store.commit(db, s)
        model.files.append(LibraryModelFile(stored_file_id=stored.id, filename=name, position=_next_position(model.files)))
    for s in staged.images:
        stored = file_store.commit(db, s)
        model.images.append(LibraryModelImage(stored_file_id=stored.id, position=_next_position(model.images)))
    db.flush()


def delete_model(db: Session, model: LibraryModel) -> None:
    """Delete a model; copies lose their 'copied from' link, unreferenced blobs are removed."""
    stored_ids = [f.stored_file_id for f in model.files] + [i.stored_file_id for i in model.images]
    db.query(LibraryModel).filter(LibraryModel.copied_from_id == model.id).update(
        {LibraryModel.copied_from_id: None}, synchronize_session=False
    )
    model_id = model.id
    model.tags = []
    db.delete(model)
    db.flush()
    file_store.release(db, stored_ids)
    reindex(db, [model_id])


def copy_model(db: Session, src: LibraryModel, user: User, category_id, tag_names: list[str]) -> LibraryModel:
    """Copy a shared model into user's library: same fields and blobs, the user's own category and tags, private."""
    cat = owned_category(db, user.id, category_id)
    copy = LibraryModel(
        user_id=user.id,
        title=src.title,
        source_url=src.source_url,
        platform=src.platform,
        author=src.author,
        license=src.license,
        description=src.description,
        print_settings=src.print_settings,
        category_id=cat.id if cat else None,
        is_shared=False,
        copied_from_id=src.id,
    )
    copy.files = [LibraryModelFile(stored_file_id=f.stored_file_id, filename=f.filename, position=f.position) for f in src.files]
    copy.images = [LibraryModelImage(stored_file_id=i.stored_file_id, position=i.position) for i in src.images]
    db.add(copy)
    db.flush()
    set_tags(db, copy, tag_names)
    reindex(db, [copy.id])
    return copy
