import re

from app.database import SessionLocal
from app.models import LibraryCategory
from tests.helpers import add_model, add_user, csrf, do_setup, login, model_ids


def _cats(client):
    html = client.get("/models/categories").text
    return {m.group(1): int(m.group(2)) for m in re.finditer(r'name="name" value="([^"]*)" form="cat-(\d+)"', html)}


def _tags(client):
    html = client.get("/models/tags").text
    return {m.group(1): int(m.group(2)) for m in re.finditer(r'name="name" value="([^"]*)" form="tag-(\d+)"', html)}


def _add_cat(client, name, parent_id=""):
    token = csrf(client, "/models/categories")
    return client.post("/models/categories", data={"name": name, "parent_id": parent_id, "csrf_token": token})


def test_create_rename_move_delete_category(client):
    do_setup(client)
    _add_cat(client, "Workshop")
    ws = _cats(client)["Workshop"]
    _add_cat(client, "Holders", ws)
    holders = _cats(client)["Holders"]
    mid = add_model(client, title="Rack", category_id=holders)
    assert model_ids(client, f"/models?cat={ws}") == [mid]  # descendant included
    token = csrf(client, "/models/categories")
    client.post(f"/models/categories/{holders}", data={"name": "Racks", "parent_id": "", "csrf_token": token})
    assert "Racks" in _cats(client)
    assert model_ids(client, f"/models?cat={ws}") == []
    assert model_ids(client, "/models?q=racks") == [mid]  # search sees new path
    client.post(f"/models/categories/{holders}/delete", data={"csrf_token": token})
    assert model_ids(client, "/models?cat=none") == [mid]
    assert model_ids(client, "/models?q=racks") == []


def test_duplicate_category_name_reports_error(client):
    do_setup(client)
    _add_cat(client, "Toys")
    r = _add_cat(client, "toys")
    assert r.status_code == 303
    assert "already exists" in client.get(r.headers["location"]).text
    assert list(_cats(client)) == ["Toys"]


def test_cycle_move_rejected(client):
    do_setup(client)
    _add_cat(client, "A")
    a = _cats(client)["A"]
    _add_cat(client, "B", a)
    b = _cats(client)["B"]
    token = csrf(client, "/models/categories")
    client.post(f"/models/categories/{a}", data={"name": "A", "parent_id": b, "csrf_token": token})
    with SessionLocal() as db:
        assert db.get(LibraryCategory, a).parent_id is None
        assert db.get(LibraryCategory, b).parent_id == a


def test_tag_rename_merge_and_delete(client):
    do_setup(client)
    m1 = add_model(client, title="One", tags="gift")
    m2 = add_model(client, title="Two", tags="present")
    tags = _tags(client)
    token = csrf(client, "/models/tags")
    client.post(f"/models/tags/{tags['present']}", data={"name": "gift", "csrf_token": token})
    assert list(_tags(client)) == ["gift"]
    assert model_ids(client, f"/models?tag={tags['gift']}") == [m1, m2]
    assert model_ids(client, "/models?q=present") == []
    client.post(f"/models/tags/{tags['gift']}/delete", data={"csrf_token": token})
    assert f"tag={tags['gift']}" not in client.get(f"/models/{m1}").text
    assert model_ids(client, "/models?q=gift") == []


def test_other_user_cannot_touch_categories_or_tags(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    _add_cat(client, "Mine")
    cid = _cats(client)["Mine"]
    add_model(client, title="T", tags="keep")
    tid = _tags(client)["keep"]
    token = csrf(client2, "/models/categories")
    client2.post(f"/models/categories/{cid}", data={"name": "Hacked", "parent_id": "", "csrf_token": token})
    client2.post(f"/models/categories/{cid}/delete", data={"csrf_token": token})
    client2.post(f"/models/tags/{tid}", data={"name": "hacked", "csrf_token": token})
    client2.post(f"/models/tags/{tid}/delete", data={"csrf_token": token})
    assert "Mine" in _cats(client)
    assert "keep" in _tags(client)
    assert _cats(client2) == {} and _tags(client2) == {}
