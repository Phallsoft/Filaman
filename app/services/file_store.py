"""Content-addressed blob store for model library files and images.

Blobs live at FILES_DIR/<sha256[:2]>/<sha256> and are shared by every
library_model_files / library_model_images row that references them. There is
no reference counter: release() deletes a blob once nothing points at it.

Writing is two-step so slow work never happens while a request holds SQLite's
write lock: stage_*() copies and hashes into FILES_DIR/tmp without touching the
database, then commit() files the staged blob and its row in a short step.
"""
import hashlib
import mimetypes
import os
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile
from sqlalchemy import update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import config
from ..models import LibraryModelFile, LibraryModelImage, StoredFile

CHUNK = 1 << 20


class FileTooLarge(Exception):
    def __init__(self, filename: str | None):
        super().__init__(f"{filename or 'File'} is too large.")
        self.filename = filename


@dataclass
class Staged:
    tmp: Path
    sha256: str
    size: int
    content_type: str


def files_root() -> Path:
    root = Path(config.FILES_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def blob_path(sha256: str) -> Path:
    return files_root() / sha256[:2] / sha256


def exists(stored: StoredFile) -> bool:
    return blob_path(stored.sha256).is_file()


def guess_type(filename: str | None, declared: str | None) -> str:
    if declared and declared != "application/octet-stream":
        return declared[:128]
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"


def _tmp_path() -> Path:
    tmp_dir = files_root() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / uuid.uuid4().hex


def stage_file(fileobj: BinaryIO, filename: str | None, content_type: str, max_bytes: int) -> Staged:
    """Copy and hash a file into FILES_DIR/tmp. No database access. Raises FileTooLarge past max_bytes."""
    tmp = _tmp_path()
    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            while chunk := fileobj.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLarge(filename)
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return Staged(tmp, digest.hexdigest(), size, content_type)


def stage_bytes(data: bytes, content_type: str) -> Staged:
    tmp = _tmp_path()
    tmp.write_bytes(data)
    return Staged(tmp, hashlib.sha256(data).hexdigest(), len(data), content_type)


def discard(staged: Iterable[Staged]) -> None:
    for s in staged:
        s.tmp.unlink(missing_ok=True)


def commit(db: Session, staged: Staged) -> StoredFile:
    """File a staged blob and return its row, reusing an identical stored file when one exists."""
    existing = db.query(StoredFile).filter(StoredFile.sha256 == staged.sha256).first()
    if existing is not None:
        # A no-op UPDATE takes the write lock and proves the row still exists: another request
        # may have released it after we read it. Once locked, nobody can release it until we commit.
        touched = db.execute(
            update(StoredFile).where(StoredFile.id == existing.id).values(size=StoredFile.size)
        ).rowcount
        if touched == 1:
            dest = blob_path(staged.sha256)
            if dest.is_file():
                staged.tmp.unlink(missing_ok=True)
            else:  # heal a blob lost from disk (e.g. after restoring an older backup)
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged.tmp, dest)
            return existing
        db.expunge(existing)
    dest = blob_path(staged.sha256)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged.tmp, dest)
    stored = StoredFile(sha256=staged.sha256, size=staged.size, content_type=staged.content_type)
    db.add(stored)
    db.flush()
    return stored


async def store_upload(db: Session, upload: UploadFile, max_bytes: int) -> StoredFile:
    """Stage an upload off the event loop, then commit it."""
    staged = await run_in_threadpool(
        stage_file, upload.file, upload.filename, guess_type(upload.filename, upload.content_type), max_bytes
    )
    return commit(db, staged)


def store_bytes(db: Session, data: bytes, content_type: str) -> StoredFile:
    return commit(db, stage_bytes(data, content_type))


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
