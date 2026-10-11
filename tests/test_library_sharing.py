import re

import pytest

from tests.helpers import add_model, add_user, csrf, do_setup, login, model_ids


@pytest.fixture()
def two_users(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    return client, client2


def _share(client, mid, on=True):
    token = csrf(client, f"/models/{mid}")
    client.post(f"/models/{mid}/share", data={"shared": "1" if on else "0", "csrf_token": token})


def _copy(client, mid, **data):
    token = csrf(client, f"/models/{mid}")
    r = client.post(f"/models/{mid}/copy", data={"csrf_token": token, **data})
    assert r.status_code == 303
    return int(re.match(r"/models/(\d+)$", r.headers["location"]).group(1))


def test_shared_model_visible_read_only(two_users):
    a, b = two_users
    mid = add_model(a, title="Clip", tags="secret-tag", new_category="A-cat",
                    files=[("files", ("clip.stl", b"clip", "model/stl"))])
    assert f'href="/models/{mid}"' not in b.get("/models/shared").text
    _share(a, mid)
    assert f'href="/models/{mid}"' in b.get("/models/shared").text
    page = b.get(f"/models/{mid}").text
    assert "Clip" in page and "secret-tag" not in page and "A-cat" not in page
    assert f"/models/{mid}/edit" not in page
    fid = re.search(r'href="/models/files/(\d+)"', page).group(1)
    assert b.get(f"/models/files/{fid}").content == b"clip"
    assert b.get(f"/models/{mid}/edit").status_code == 404


def test_share_toggle_is_owner_only(two_users):
    a, b = two_users
    mid = add_model(a, title="Mine")
    token = csrf(b, "/models")
    r = b.post(f"/models/{mid}/share", data={"shared": "1", "csrf_token": token})
    assert r.status_code == 404
    assert f'href="/models/{mid}"' not in b.get("/models/shared").text


def test_copy_is_independent_and_private(two_users):
    a, b = two_users
    mid = add_model(a, title="Clip", tags="a-tag", new_category="A-cat")
    _share(a, mid)
    cid = _copy(b, mid, tags="mine")
    assert cid != mid and model_ids(b) == [cid]
    page = b.get(f"/models/{cid}").text
    assert "mine" in page and "a-tag" not in page and "A-cat" not in page
    assert "copied from" in page.lower() and "admin" in page
    assert f'href="/models/{cid}"' not in a.get("/models/shared").text  # copy starts private
    assert "In your library" in b.get("/models/shared").text


def test_copy_into_own_category_and_edit_does_not_touch_original(two_users):
    a, b = two_users
    mid = add_model(a, title="Original")
    _share(a, mid)
    token = csrf(b, "/models")
    b.post("/models/quick", data={"url": "", "title": "seed", "category_id": "", "csrf_token": token})
    cid = _copy(b, mid, new_category="Bob-cat")
    assert "Bob-cat" in b.get(f"/models/{cid}").text
    token = csrf(b, f"/models/{cid}/edit")
    b.post(f"/models/{cid}", data={"title": "Bob's version", "csrf_token": token})
    assert "Original" in a.get(f"/models/{mid}").text


def test_copy_survives_original_delete_and_download_works(two_users):
    a, b = two_users
    mid = add_model(a, title="Hinge", files=[("files", ("hinge.stl", b"hinge", "model/stl"))])
    _share(a, mid)
    cid = _copy(b, mid)
    token = csrf(a, f"/models/{mid}")
    a.post(f"/models/{mid}/delete", data={"csrf_token": token})
    page = b.get(f"/models/{cid}").text
    assert "Hinge" in page
    fid = re.search(r'href="/models/files/(\d+)"', page).group(1)
    assert b.get(f"/models/files/{fid}").content == b"hinge"


def test_unshared_model_cannot_be_copied(two_users):
    a, b = two_users
    mid = add_model(a, title="Private")
    token = csrf(b, "/models")
    r = b.post(f"/models/{mid}/copy", data={"csrf_token": token})
    assert r.status_code == 404
    assert model_ids(b) == []


def test_unshare_hides_but_keeps_copies(two_users):
    a, b = two_users
    mid = add_model(a, title="Temp")
    _share(a, mid)
    cid = _copy(b, mid)
    _share(a, mid, on=False)
    assert b.get(f"/models/{mid}").status_code == 404
    assert b.get(f"/models/{cid}").status_code == 200


def test_shared_search_and_owner_filter(two_users):
    a, b = two_users
    m1 = add_model(a, title="Planter")
    m2 = add_model(a, title="Lamp")
    m3 = add_model(b, title="Plant label")
    for client, mid in ((a, m1), (a, m2), (b, m3)):
        _share(client, mid)
    html = b.get("/models/shared?q=plant").text
    assert f'href="/models/{m1}"' in html and f'href="/models/{m3}"' in html and f'href="/models/{m2}"' not in html
    owner_id = re.search(r'name="owner"[\s\S]*?<option value="(\d+)"[^>]*>admin<', html).group(1)
    html = b.get(f"/models/shared?owner={owner_id}").text
    assert f'href="/models/{m3}"' not in html and f'href="/models/{m1}"' in html
    assert "yours" in b.get("/models/shared").text
