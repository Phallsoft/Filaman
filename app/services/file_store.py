"""Content-addressed blob store for model library files and images.

Blobs live at FILES_DIR/<sha256[:2]>/<sha256> and are shared by every
library_model_files / library_model_images row that references them. There is
no reference counter: release() deletes a blob once nothing points at it.
"""
import hashlib
import mimetypes
import os
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from .. import config
from ..models import LibraryModelFile, LibraryModelImage, StoredFile

CHUNK = 1 << 20


class FileTooLarge(Exception):
    def __init__(self, filename: str | None):
        super().__init__(f"{filename or 'File'} is too large.")
        self.filename = filename


def files_root() -> Path:
    root = Path(config.FILES_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def blob_path(sha256: str) -> Path:
    return files_root() / sha256[:2] / sha256


def exists(stored: StoredFile) -> bool:
    return blob_path(stored.sha256).is_file()


def _guess_type(filename: str | None, declared: str | None) -> str:
    if declared and declared != "application/octet-stream":
        return declared[:128]
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"


def _commit_blob(db: Session, tmp: Path, sha: str, size: int, content_type: str) -> StoredFile:
    existing = db.query(StoredFile).filter(StoredFile.sha256 == sha).first()
    if existing:
        if exists(existing):
            tmp.unlink(missing_ok=True)
        else:  # heal a blob lost from disk (e.g. after restoring an older backup)
            blob_path(sha).parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, blob_path(sha))
        return existing
    dest = blob_path(sha)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, dest)
    stored = StoredFile(sha256=sha, size=size, content_type=content_type)
    db.add(stored)
    db.flush()
    return stored


def _tmp_path() -> Path:
    tmp_dir = files_root() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / uuid.uuid4().hex


async def store_upload(db: Session, upload: UploadFile, max_bytes: int) -> StoredFile:
    """Stream an upload into the store, hashing as it goes. Raises FileTooLarge past max_bytes."""
    tmp = _tmp_path()
    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            while chunk := await upload.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLarge(upload.filename)
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return _commit_blob(db, tmp, digest.hexdigest(), size, _guess_type(upload.filename, upload.content_type))


def store_bytes(db: Session, data: bytes, content_type: str) -> StoredFile:
    tmp = _tmp_path()
    tmp.write_bytes(data)
    return _commit_blob(db, tmp, hashlib.sha256(data).hexdigest(), len(data), content_type)


def release(db: Session, ids: Iterable[int]) -> None:
    """Delete each stored file (row and blob) that no model file or image references any more."""
    db.flush()
    for stored_id in set(ids):
        in_use = (
            db.query(LibraryModelFile.id).filter(LibraryModelFile.stored_file_id == stored_id).first()
            or db.query(LibraryModelImage.id).filter(LibraryModelImage.stored_file_id == stored_id).first()
        )
        if in_use:
            continue
        stored = db.get(StoredFile, stored_id)
        if stored:
            blob_path(stored.sha256).unlink(missing_ok=True)
            db.delete(stored)
    db.flush()


def clear_all() -> None:
    shutil.rmtree(files_root(), ignore_errors=True)
    files_root()
