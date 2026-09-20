import pytest

from tests.helpers import add_lookup, add_user, csrf, do_setup, login, lookup_names


@pytest.fixture()
def two_users(client, client2):
    """client = admin ('admin'), client2 = 'bob'. Both logged in."""
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    return client, client2


@pytest.mark.parametrize("entity", ["manufacturers", "materials", "colors"])
def test_lookups_are_per_user(two_users, entity):
    a, b = two_users
    add_lookup(a, entity, "Alpha")
    add_lookup(b, entity, "Beta")
    assert lookup_names(a, entity) == ["Alpha"]
    assert lookup_names(b, entity) == ["Beta"]


def test_same_lookup_name_allowed_for_different_users(two_users):
    a, b = two_users
    add_lookup(a, "materials", "PLA")
    add_lookup(b, "materials", "PLA")
    assert lookup_names(a, "materials") == ["PLA"]
    assert lookup_names(b, "materials") == ["PLA"]


def test_cannot_edit_or_delete_other_users_lookup(two_users):
    a, b = two_users
    mid = add_lookup(a, "manufacturers", "Prusa")
    token = csrf(b, "/manufacturers")
    b.post(f"/manufacturers/{mid}", data={"name": "Hacked", "csrf_token": token})
    b.post(f"/manufacturers/{mid}/delete", data={"csrf_token": token})
    assert lookup_names(a, "manufacturers") == ["Prusa"]


def test_quick_create_only_returns_own_items(two_users):
    a, b = two_users
    add_lookup(a, "colors", "Red")
    token = csrf(b, "/colors")
    r = b.post("/colors/quick", data={"qname_colors": "Blue", "csrf_token": token})
    assert "Blue" in r.text and "Red" not in r.text


from tests.helpers import add_spool, spool_ids, spool_qty


def _seed(client):
    """Create one manufacturer/material/color for a client; return their ids."""
    return (
        add_lookup(client, "manufacturers", "Mfg"),
        add_lookup(client, "materials", "PLA"),
        add_lookup(client, "colors", "Black"),
    )


def test_spools_are_per_user(two_users):
    a, b = two_users
    sa = add_spool(a, *_seed(a))
    sb = add_spool(b, *_seed(b))
    assert spool_ids(a) == [sa]
    assert spool_ids(b) == [sb]


def test_cannot_touch_other_users_spool(two_users):
    a, b = two_users
    sa = add_spool(a, *_seed(a), qty=3)
    _seed(b)
    token = csrf(b, "/spools")
    assert b.get(f"/spools/{sa}/edit").status_code in (303, 404)
    b.post(f"/spools/{sa}/adjust", data={"delta": 5, "csrf_token": token})
    b.post(f"/spools/{sa}/delete", data={"csrf_token": token})
    mb, tb, cb = _seed(b)
    b.post(f"/spools/{sa}", data={
        "manufacturer_id": mb, "material_type_id": tb, "color_id": cb,
        "weight": 1, "qty": 0, "csrf_token": token,
    })
    assert spool_ids(a) == [sa]
    assert spool_qty(a, sa) == 3


def test_cannot_create_spool_with_other_users_lookups(two_users):
    a, b = two_users
    ma, ta, ca = _seed(a)
    token = csrf(b, "/spools/new")
    r = b.post("/spools", data={
        "manufacturer_id": ma, "material_type_id": ta, "color_id": ca,
        "weight": 1000, "qty": 1, "csrf_token": token,
    })
    assert r.status_code == 303
    assert spool_ids(b) == []


def test_merge_only_within_user(two_users):
    a, b = two_users
    ma, ta, ca = _seed(a)
    mb, tb, cb = _seed(b)
    s1 = add_spool(a, ma, ta, ca, qty=1)
    s2 = add_spool(a, ma, ta, ca, qty=2)   # identical -> merges into s1
    assert s1 == s2 and spool_qty(a, s1) == 3
    sb = add_spool(b, mb, tb, cb, qty=1)   # same names, different user -> separate
    assert sb != s1 and spool_qty(b, sb) == 1


def test_ai_import_save_creates_lookups_for_importing_user_only(two_users):
    a, b = two_users
    token = csrf(b, "/import")
    r = b.post("/import/save", data={
        "manufacturer": "Polymaker", "material": "PETG", "color_name": "Teal",
        "color_hex": "#008080", "weight": 1000, "qty": 2, "csrf_token": token,
    })
    assert r.status_code == 303 and r.headers["location"] == "/spools"
    assert lookup_names(b, "manufacturers") == ["Polymaker"]
    assert lookup_names(a, "manufacturers") == []
    assert len(spool_ids(b)) == 1 and spool_ids(a) == []
