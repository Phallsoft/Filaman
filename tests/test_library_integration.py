import sqlite3

from app.database import SessionLocal
from app.models import LibraryCategory, LibraryModel, LibraryTag, StoredFile, User
from tests.helpers import add_model, add_user, csrf, do_setup, login


def _bob_id():
    with SessionLocal() as db:
        return db.query(User).filter_by(username="bob").one().id


def test_deleting_user_removes_library_and_blobs(client, client2, files_dir):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    add_model(client2, title="Bob model", tags="t", new_category="C",
              files=[("files", ("b.stl", b"bob bytes", "model/stl"))])
    token = csrf(client, "/admin")
    r = client.post(f"/admin/users/{_bob_id()}/delete", data={"confirm_text": "bob", "csrf_token": token})
    assert "deleted" in client.get(r.headers["location"]).text
    with SessionLocal() as db:
        assert db.query(LibraryModel).count() == 0
        assert db.query(LibraryTag).count() == 0
        assert db.query(LibraryCategory).count() == 0
        assert db.query(StoredFile).count() == 0
    assert not any(p.is_file() for p in files_dir.rglob("*"))


def test_deleting_user_keeps_blobs_other_users_copied(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    mid = add_model(client2, title="Shared", files=[("files", ("s.stl", b"shared bytes", "model/stl"))])
    token = csrf(client2, f"/models/{mid}")
    client2.post(f"/models/{mid}/share", data={"shared": "1", "csrf_token": token})
    token = csrf(client, f"/models/{mid}")
    r = client.post(f"/models/{mid}/copy", data={"csrf_token": token})
    copy_id = int(r.headers["location"].rsplit("/", 1)[1])
    token = csrf(client, "/admin")
    r = client.post(f"/admin/users/{_bob_id()}/delete", data={"confirm_text": "bob", "csrf_token": token})
    assert "deleted" in client.get(r.headers["location"]).text
    page = client.get(f"/models/{copy_id}").text
    assert "Shared" in page and "file missing" not in page


def test_admin_reset_clears_files_dir(client, files_dir):
    do_setup(client)
    add_model(client, title="X", files=[("files", ("x.stl", b"x bytes", "model/stl"))])
    assert any(p.is_file() for p in files_dir.rglob("*"))
    token = csrf(client, "/admin")
    client.post("/admin/reset", data={"confirm_text": "Yes, Really Reset The Database", "csrf_token": token})
    assert not any(p.is_file() for p in files_dir.rglob("*"))


def test_upgrade_adds_library_tables(client):
    from app.config import DB_PATH
    from app.database import engine, init_db

    do_setup(client)
    with engine.begin() as conn:
        for t in ["library_model_tags", "library_model_files", "library_model_images", "library_models",
                  "library_tags", "library_categories", "stored_files", "library_search"]:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {t}")
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        names = {r[0] for r in conn.execute("select name from sqlite_master")}
    finally:
        conn.close()
    assert {"library_models", "stored_files", "library_search", "spools"} <= names
    assert client.get("/models").status_code == 200
