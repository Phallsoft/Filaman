"""Regression tests for findings from the final branch review."""
import re
import sqlite3
from io import BytesIO

import pytest
from PIL import Image

from app.config import DB_PATH
from app.database import SessionLocal
from app.models import LibraryCategory, StoredFile, User
from app.services import file_store
from app.services import library_taxonomy as tx
from tests.helpers import add_model, add_user, csrf, do_setup, login, model_ids


def _png(color="red", size=(40, 20)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _db_write_lock_free() -> bool:
    """True if another connection can take SQLite's write lock right now."""
    conn = sqlite3.connect(DB_PATH, timeout=0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


@pytest.fixture()
def lock_probe(monkeypatch):
    """Record whether the DB write lock was free while each upload was being staged."""
    seen = []
    original = file_store.stage_file

    def probing(*args, **kwargs):
        seen.append(_db_write_lock_free())
        return original(*args, **kwargs)

    monkeypatch.setattr(file_store, "stage_file", probing)
    return seen


# Important 1: uploads must not run while this request holds the SQLite write lock.
def test_create_does_not_hold_db_lock_while_staging_uploads(client, lock_probe):
    do_setup(client)
    add_model(client, title="Big", tags="new-tag", new_category="New cat",
              files=[("files", ("big.stl", b"x" * 5000, "model/stl"))])
    assert lock_probe == [True]


def test_update_does_not_hold_db_lock_while_staging_uploads(client, lock_probe):
    do_setup(client)
    mid = add_model(client, title="Kit")
    token = csrf(client, f"/models/{mid}/edit")
    r = client.post(f"/models/{mid}", data={"title": "Kit", "tags": "brand-new", "new_category": "Fresh",
                                           "csrf_token": token},
                    files=[("files", ("more.stl", b"y" * 5000, "model/stl"))])
    assert r.status_code == 303
    assert lock_probe == [True]


# Important 2: a stored-file row deleted by another request between lookup and use.
def test_commit_reinserts_row_deleted_concurrently(client):
    with SessionLocal() as setup:
        original = file_store.commit(setup, file_store.stage_bytes(b"racy bytes", "model/stl"))
        setup.commit()
        orig_id = original.id
    with SessionLocal() as a:
        a.query(StoredFile).filter_by(id=orig_id).one()  # request A has seen the row
        with SessionLocal() as b:  # request B releases it and commits
            b.query(StoredFile).filter_by(id=orig_id).delete()
            b.commit()
        file_store.blob_path(original.sha256).unlink()
        stored = file_store.commit(a, file_store.stage_bytes(b"racy bytes", "model/stl"))
        a.commit()
        assert a.query(StoredFile).filter_by(id=stored.id).count() == 1
        assert file_store.exists(stored)


# Important 2/3: ids are never reused, so stale references and browser caches can't alias new content.
def test_ids_are_not_reused_after_delete(client):
    do_setup(client)
    mid = add_model(client, title="Pics", files=[("images", ("a.png", _png("red"), "image/png"))])
    first = int(re.search(r'src="/models/images/(\d+)', client.get(f"/models/{mid}").text).group(1))
    token = csrf(client, f"/models/{mid}/edit")
    client.post(f"/models/{mid}/images/{first}/delete", data={"csrf_token": token})
    client.post(f"/models/{mid}", data={"title": "Pics", "csrf_token": token},
                files=[("images", ("b.png", _png("blue"), "image/png"))])
    second = int(re.search(r'src="/models/images/(\d+)', client.get(f"/models/{mid}").text).group(1))
    assert second != first
    with SessionLocal() as db:
        assert db.query(StoredFile).one().id != 1


def test_images_served_as_jpeg_with_nosniff(client):
    do_setup(client)
    mid = add_model(client, title="Pic", files=[("images", ("a.png", _png(), "image/png"))])
    iid = re.search(r'src="/models/images/(\d+)', client.get(f"/models/{mid}").text).group(1)
    r = client.get(f"/models/images/{iid}")
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers.get("x-content-type-options") == "nosniff"


# Important 4: shared search must not match the owner's private tags or category path.
def test_shared_search_ignores_private_tags_and_categories(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    mid = add_model(client, title="Plain box", tags="surprise-for-bob", new_category="Gifts")
    token = csrf(client, f"/models/{mid}")
    client.post(f"/models/{mid}/share", data={"shared": "1", "csrf_token": token})
    assert model_ids(client2, "/models/shared?q=surprise") == []
    assert model_ids(client2, "/models/shared?q=gifts") == []
    assert model_ids(client2, "/models/shared?q=plain") == [mid]
    assert model_ids(client, "/models?q=surprise") == [mid]  # owner's own search still uses them


# Re-graded minor 2: an oversized image is reported, not a 500 that loses the form.
def test_decompression_bomb_image_reported_not_500(client, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)  # 40x20 = 800 px > 2x limit -> bomb error
    do_setup(client)
    token = csrf(client, "/models/new")
    r = client.post("/models", data={"title": "Huge render", "csrf_token": token},
                    files=[("images", ("render.png", _png(), "image/png"))])
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "Huge render" in page and "render.png" in page


# Re-graded minor 4: deleting a category must not rename a child just because it shares the parent's name.
def test_delete_category_keeps_child_named_like_parent(client):
    do_setup(client)
    with SessionLocal() as db:
        uid = db.query(User).first().id
        tools = tx.create_category(db, uid, "Tools")
        child = tx.create_category(db, uid, "Tools", tools.id)
        other = tx.create_category(db, uid, "Bits")
        clash = tx.create_category(db, uid, "Bits", tools.id)
        clash2 = tx.create_category(db, uid, "Bits (Tools)")
        db.commit()
        tx.delete_category(db, tools)
        db.commit()
        names = sorted(c.name for c in db.query(LibraryCategory).filter_by(user_id=uid))
        assert db.get(LibraryCategory, child.id).name == "Tools"
        assert len(names) == len(set(n.lower() for n in names)), names
        assert db.get(LibraryCategory, clash.id).parent_id is None
        _ = (other, clash2)
