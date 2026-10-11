import asyncio
import hashlib
from io import BytesIO

import pytest
from fastapi import UploadFile

from app.database import SessionLocal
from app.models import LibraryModel, LibraryModelFile, StoredFile, User
from app.services import file_store
from tests.helpers import do_setup


def _upload(data: bytes, name="part.stl") -> UploadFile:
    return UploadFile(file=BytesIO(data), filename=name)


def _store(db, data: bytes, max_bytes=10_000):
    return asyncio.run(file_store.store_upload(db, _upload(data), max_bytes))


def test_store_upload_writes_blob_by_hash(client, files_dir):
    with SessionLocal() as db:
        stored = _store(db, b"solid cube")
        db.commit()
        sha = hashlib.sha256(b"solid cube").hexdigest()
        assert stored.sha256 == sha and stored.size == 10
        assert file_store.blob_path(sha) == files_dir / sha[:2] / sha
        assert file_store.blob_path(sha).read_bytes() == b"solid cube"


def test_duplicate_upload_is_stored_once(client):
    with SessionLocal() as db:
        a = _store(db, b"same bytes")
        b = _store(db, b"same bytes")
        db.commit()
        assert a.id == b.id
        assert db.query(StoredFile).count() == 1


def test_too_large_upload_raises_and_leaves_no_temp(client, files_dir):
    with SessionLocal() as db:
        with pytest.raises(file_store.FileTooLarge) as exc:
            _store(db, b"x" * 101, max_bytes=100)
        assert exc.value.filename == "part.stl"
        assert db.query(StoredFile).count() == 0
    tmp = files_dir / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


def _model_with_file(db, user_id, stored, title="M"):
    m = LibraryModel(user_id=user_id, title=title)
    m.files.append(LibraryModelFile(stored_file_id=stored.id, filename="a.stl", position=0))
    db.add(m)
    db.flush()
    return m


def test_release_keeps_blob_while_referenced(client):
    do_setup(client)
    with SessionLocal() as db:
        uid = db.query(User).first().id
        stored = _store(db, b"shared blob")
        m1 = _model_with_file(db, uid, stored, "one")
        _model_with_file(db, uid, stored, "two")
        db.delete(m1)
        db.flush()
        file_store.release(db, [stored.id])
        db.commit()
        assert db.get(StoredFile, stored.id) is not None
        assert file_store.blob_path(stored.sha256).exists()


def test_release_deletes_unreferenced_blob(client):
    do_setup(client)
    with SessionLocal() as db:
        uid = db.query(User).first().id
        stored = _store(db, b"lonely blob")
        m = _model_with_file(db, uid, stored)
        sha = stored.sha256
        db.delete(m)
        db.flush()
        file_store.release(db, [stored.id])
        db.commit()
        assert db.query(StoredFile).count() == 0
        assert not file_store.blob_path(sha).exists()


def test_store_bytes_and_clear_all(client, files_dir):
    with SessionLocal() as db:
        stored = file_store.store_bytes(db, b"\xff\xd8jpeg", "image/jpeg")
        db.commit()
        assert file_store.exists(stored)
    file_store.clear_all()
    assert not any(p for p in files_dir.rglob("*") if p.is_file())
