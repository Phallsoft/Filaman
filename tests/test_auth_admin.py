from tests.helpers import do_setup, login, logout

import sqlite3
import os

from tests.helpers import add_user, csrf


def test_setup_then_login(client):
    assert client.get("/spools").headers["location"] == "/setup"
    do_setup(client, "admin", "password123")
    assert client.get("/spools").status_code == 200
    logout(client)
    assert client.get("/spools").headers["location"] == "/login"
    login(client, "admin", "password123")
    assert client.get("/spools").status_code == 200


def _user_row(username):
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        return conn.execute(
            "SELECT id, is_admin FROM users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()


def test_setup_user_is_admin_and_sees_admin_nav(client):
    do_setup(client)
    assert _user_row("admin")[1] == 1
    html = client.get("/spools").text
    assert 'href="/admin"' in html
    assert 'href="/settings"' in html


def test_admin_can_add_user_and_new_user_is_not_admin(client, client2):
    do_setup(client)
    add_user(client, "bob", "bobpass123")
    assert _user_row("bob")[1] == 0
    login(client2, "bob", "bobpass123")
    html = client2.get("/spools").text
    assert 'href="/admin"' not in html
    assert 'href="/settings"' in html


def test_add_user_rejects_duplicate_and_short_password(client):
    do_setup(client)
    add_user(client, "bob")
    token = csrf(client, "/admin")
    client.post("/admin/users", data={"username": "bob", "password": "password123", "csrf_token": token})
    client.post("/admin/users", data={"username": "carol", "password": "short", "csrf_token": token})
    assert _user_row("carol") is None
    html = client.get("/admin").text
    assert "already exists" in html or "already taken" in html


def test_non_admin_gets_403_on_admin_routes(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    assert client2.get("/admin").status_code == 403
    token = csrf(client2, "/settings")
    assert client2.post("/admin/users", data={"username": "x", "password": "password123", "csrf_token": token}).status_code == 403
    assert client2.get("/admin/backup").status_code == 403
    assert client2.post("/admin/reset", data={"confirm_text": "", "csrf_token": token}).status_code == 403


def test_settings_change_password(client):
    do_setup(client, "admin", "password123")
    token = csrf(client, "/settings")
    r = client.post("/settings/password", data={
        "current_password": "password123", "new_password": "newpass456",
        "confirm_password": "newpass456", "csrf_token": token,
    })
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    logout(client)
    login(client, "admin", "newpass456")


def test_admin_reset_user_password(client, client2):
    do_setup(client)
    add_user(client, "bob", "password123")
    uid = _user_row("bob")[0]
    token = csrf(client, "/admin")
    r = client.post(f"/admin/users/{uid}/password", data={"new_password": "changed789", "csrf_token": token})
    assert r.status_code == 303
    login(client2, "bob", "changed789")


def test_admin_delete_user_and_cannot_delete_self(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    bob_id = _user_row("bob")[0]
    admin_id = _user_row("admin")[0]
    token = csrf(client, "/admin")
    # wrong confirmation phrase -> no delete
    client.post(f"/admin/users/{bob_id}/delete", data={"confirm_text": "nope", "csrf_token": token})
    assert _user_row("bob") is not None
    client.post(f"/admin/users/{bob_id}/delete", data={"confirm_text": "bob", "csrf_token": token})
    assert _user_row("bob") is None
    # bob's live session is now invalid -> bounced to login
    assert client2.get("/spools").headers["location"] == "/login"
    client.post(f"/admin/users/{admin_id}/delete", data={"confirm_text": "admin", "csrf_token": token})
    assert _user_row("admin") is not None


from tests.helpers import add_lookup, add_spool, lookup_names, spool_ids


def test_delete_user_removes_their_data_and_images(client, client2, media_dir):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    m = add_lookup(client2, "manufacturers", "M")
    t = add_lookup(client2, "materials", "T")
    c = add_lookup(client2, "colors", "C")
    sid = add_spool(client2, m, t, c)
    # give the spool an image file on disk
    img_dir = media_dir / "spools"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / "bobpic.jpg").write_bytes(b"x")
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.execute("UPDATE spools SET image_path='spools/bobpic.jpg' WHERE id=?", (sid,))
    conn.commit()
    bob_id = _user_row("bob")[0]
    token = csrf(client, "/admin")
    client.post(f"/admin/users/{bob_id}/delete", data={"confirm_text": "bob", "csrf_token": token})
    assert _user_row("bob") is None
    assert not (img_dir / "bobpic.jpg").exists()
    for table in ("spools", "spool_inventory", "manufacturers", "material_types", "colors"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    conn.close()


from app.auth import hash_password
from tests.test_migration import OLD_DATA, OLD_SCHEMA


def _old_schema_db_bytes(tmp_path, password="password123", dangling=False) -> bytes:
    """A pre-multi-user backup with a real password hash for 'admin'."""
    path = tmp_path / "upload.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA + OLD_DATA)
    conn.execute("UPDATE users SET hashed_password = ?", (hash_password(password),))
    if dangling:
        conn.execute("DELETE FROM manufacturers WHERE id = 1")  # spool 7 now points at nothing
    conn.commit()
    conn.close()
    return path.read_bytes()


def _post_restore(client, payload: bytes):
    token = csrf(client, "/admin")
    return client.post(
        "/admin/restore",
        data={"csrf_token": token},
        files={"file": ("backup.db", payload, "application/octet-stream")},
    )


def test_restore_of_unmigratable_backup_leaves_live_db_untouched(client, tmp_path):
    do_setup(client)
    r = _post_restore(client, _old_schema_db_bytes(tmp_path, dangling=True))
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert "Restore failed" in client.get("/admin").text
    # still logged in against the unchanged live DB
    assert client.get("/spools").status_code == 200
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        assert conn.execute("SELECT username FROM users").fetchall() == [("admin",)]
    finally:
        conn.close()


def test_restore_of_old_schema_backup_migrates_it(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FILAMAN_LEGACY_OWNER", "jpaul")
    do_setup(client)
    r = _post_restore(client, _old_schema_db_bytes(tmp_path))
    assert r.status_code == 303 and r.headers["location"] == "/login"
    login(client, "admin", "password123")
    html = client.get("/admin").text
    assert "admin" in html and "jpaul" in html
    assert _user_row("jpaul")[1] == 0 and _user_row("admin")[1] == 1
