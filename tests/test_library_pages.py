import re

import pytest

from app.database import SessionLocal
from app.models import User
from app.services import library_taxonomy as tx
from tests.helpers import add_model, add_user, csrf, do_setup, login, model_ids


@pytest.fixture()
def two_users(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    return client, client2


def _make_category(name, username="admin", parent_id=None) -> int:
    with SessionLocal() as db:
        uid = db.query(User).filter_by(username=username).one().id
        cat = tx.create_category(db, uid, name, parent_id)
        db.commit()
        return cat.id


def test_nav_has_models_link(client):
    do_setup(client)
    assert 'href="/models"' in client.get("/spools").text


def test_create_with_title_only(client):
    do_setup(client)
    mid = add_model(client, title="Benchy")
    r = client.get(f"/models/{mid}")
    assert r.status_code == 200 and "Benchy" in r.text
    assert model_ids(client) == [mid]


def test_empty_library_page_renders(client):
    do_setup(client)
    r = client.get("/models")
    assert r.status_code == 200 and "No models" in r.text


def test_title_derived_from_url_and_platform_filled(client):
    do_setup(client)
    mid = add_model(client, url="https://www.printables.com/model/123-wera-bit-holder")
    page = client.get(f"/models/{mid}").text
    assert "Wera bit holder" in page and "Printables" in page


def test_missing_title_and_no_derivable_title_rejected(client):
    do_setup(client)
    token = csrf(client, "/models/new")
    r = client.post("/models", data={"title": "", "source_url": "https://www.thingiverse.com/thing:1",
                                     "description": "long notes I typed", "csrf_token": token})
    assert r.status_code == 400 and "title is required" in r.text
    assert "long notes I typed" in r.text  # input preserved
    assert model_ids(client) == []


def test_non_http_url_rejected(client):
    do_setup(client)
    token = csrf(client, "/models/new")
    r = client.post("/models", data={"title": "x", "source_url": "javascript:alert(1)", "csrf_token": token})
    assert r.status_code == 400 and "http://" in r.text
    assert model_ids(client) == []


def test_quick_add_into_category(client):
    do_setup(client)
    cat_id = _make_category("Toys")
    assert f'href="/models?cat={cat_id}"' in client.get("/models").text
    token = csrf(client, "/models")
    r = client.post("/models/quick", data={
        "url": "https://makerworld.com/en/models/55-fidget-spinner", "title": "", "category_id": cat_id,
        "csrf_token": token,
    })
    assert r.status_code == 303
    assert "Fidget spinner" in client.get(f"/models?cat={cat_id}").text
    assert "Fidget spinner" not in client.get("/models?cat=none").text


def test_quick_add_without_title_or_usable_url_rejected(client):
    do_setup(client)
    token = csrf(client, "/models")
    r = client.post("/models/quick", data={"url": "", "title": "", "csrf_token": token})
    assert r.status_code == 303
    assert model_ids(client) == []


def test_edit_updates_fields_and_tags(client):
    do_setup(client)
    mid = add_model(client, title="Old")
    token = csrf(client, f"/models/{mid}/edit")
    r = client.post(f"/models/{mid}", data={
        "title": "New", "author": "Ada", "tags": "gift, printed", "print_settings": "0.2mm\n15% infill",
        "csrf_token": token,
    })
    assert r.status_code == 303
    page = client.get(f"/models/{mid}").text
    assert "New" in page and "Ada" in page and "gift" in page and "printed" in page
    assert "15% infill" in page


def test_edit_with_blank_title_rejected(client):
    do_setup(client)
    mid = add_model(client, title="Keep")
    token = csrf(client, f"/models/{mid}/edit")
    r = client.post(f"/models/{mid}", data={"title": "  ", "author": "Typed", "csrf_token": token})
    assert r.status_code == 400 and 'value="Typed"' in r.text
    page = client.get(f"/models/{mid}").text
    assert "Keep" in page and "Typed" not in page


def test_tag_filter_and_search(client):
    do_setup(client)
    a = add_model(client, title="Alpha", tags="gift")
    b = add_model(client, title="Beta")
    tag_id = int(re.search(r'tag=(\d+)', client.get("/models").text).group(1))
    assert model_ids(client, f"/models?tag={tag_id}") == [a]
    assert model_ids(client, "/models?q=bet") == [b]


def test_category_filter_includes_descendants(client):
    do_setup(client)
    parent = _make_category("Workshop")
    child = _make_category("Holders", parent_id=parent)
    mid = add_model(client, title="Rack", category_id=child)
    assert model_ids(client, f"/models?cat={parent}") == [mid]
    assert model_ids(client, "/models?cat=none") == []


def test_foreign_category_id_ignored_on_create(two_users):
    a, b = two_users
    foreign = _make_category("A-only")
    mid = add_model(b, title="Bob's", category_id=foreign)
    assert model_ids(b, "/models?cat=none") == [mid]


def test_private_model_invisible_to_other_user(two_users):
    a, b = two_users
    mid = add_model(a, title="Secret")
    assert b.get(f"/models/{mid}").status_code == 404
    assert b.get(f"/models/{mid}/edit").status_code == 404
    token = csrf(b, "/models")
    b.post(f"/models/{mid}", data={"title": "Hacked", "csrf_token": token})
    b.post(f"/models/{mid}/delete", data={"csrf_token": token})
    assert "Secret" in a.get(f"/models/{mid}").text
    assert model_ids(b) == []


def test_delete_model(client):
    do_setup(client)
    mid = add_model(client, title="Gone")
    token = csrf(client, f"/models/{mid}")
    client.post(f"/models/{mid}/delete", data={"csrf_token": token})
    assert client.get(f"/models/{mid}").status_code == 404


def test_new_category_created_from_form(client):
    do_setup(client)
    mid = add_model(client, title="X", new_category="Garden")
    assert "Garden" in client.get(f"/models/{mid}").text


def test_pagination(client):
    do_setup(client)
    for i in range(61):
        add_model(client, title=f"M{i:02d}")
    assert len(model_ids(client)) == 60
    assert len(model_ids(client, "/models?page=2")) == 1
