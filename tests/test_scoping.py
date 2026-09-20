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
