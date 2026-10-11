import re

from app.database import SessionLocal
from app.models import User
from app.services import library_taxonomy as tx
from tests.helpers import add_model, add_user, csrf, do_setup, login, model_ids


def _bulk(client, action, ids, **extra):
    token = csrf(client, "/models")
    return client.post("/models/bulk", data={"action": action, "ids": ids, "csrf_token": token, **extra})


def _cat(name, username="admin") -> int:
    with SessionLocal() as db:
        uid = db.query(User).filter_by(username=username).one().id
        cat = tx.create_category(db, uid, name)
        db.commit()
        return cat.id


def test_bulk_move_and_tag(client):
    do_setup(client)
    a, b, c = (add_model(client, title=t) for t in ("A", "B", "C"))
    cat = _cat("Shelf")
    _bulk(client, "move", [a, b], category_id=cat)
    assert model_ids(client, f"/models?cat={cat}") == [a, b]
    _bulk(client, "add_tag", [b, c], tag="todo")
    tag_id = int(re.search(r"tag=(\d+)", client.get("/models").text).group(1))
    assert model_ids(client, f"/models?tag={tag_id}") == [b, c]
    _bulk(client, "remove_tag", [b], tag="todo")
    assert model_ids(client, f"/models?tag={tag_id}") == [c]
    _bulk(client, "move", [a], category_id="")
    assert model_ids(client, "/models?cat=none") == [a, c]


def test_bulk_add_tag_is_searchable(client):
    do_setup(client)
    a = add_model(client, title="Widget")
    _bulk(client, "add_tag", [a], tag="giftable")
    assert model_ids(client, "/models?q=giftable") == [a]


def test_bulk_share_and_delete(client):
    do_setup(client)
    a, b = add_model(client, title="A"), add_model(client, title="B")
    _bulk(client, "share", [a, b])
    assert model_ids(client, "/models/shared") == [a, b]
    _bulk(client, "unshare", [b])
    assert model_ids(client, "/models/shared") == [a]
    _bulk(client, "delete", [a, b])
    assert model_ids(client) == []


def test_bulk_ignores_foreign_ids(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    mine = add_model(client2, title="Bob's")
    theirs = add_model(client, title="Admin's")
    _bulk(client2, "delete", [mine, theirs, 99999])
    assert model_ids(client2) == []
    assert model_ids(client) == [theirs]


def test_bulk_move_to_foreign_category_ignored(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    foreign = _cat("Admin-only")
    mine = add_model(client2, title="Bob's")
    _bulk(client2, "move", [mine], category_id=foreign)
    assert model_ids(client2, "/models?cat=none") == [mine]


def test_bulk_redirect_only_to_library(client):
    do_setup(client)
    a = add_model(client, title="A")
    for target in ("https://evil.example/", "//evil.example/models", "/modelsx"):
        r = _bulk(client, "share", [a], next=target)
        assert r.headers["location"] == "/models", target
    r = _bulk(client, "share", [a], next="/models?cat=none")
    assert r.headers["location"] == "/models?cat=none"
