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
