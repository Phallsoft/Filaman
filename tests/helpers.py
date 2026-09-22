import re

_CSRF_RE = re.compile(r'name="csrf_token" value="([0-9a-f]+)"')


def csrf(client, path="/spools") -> str:
    """Fetch a page that contains a form and return the CSRF token in it."""
    r = client.get(path)
    assert r.status_code == 200, f"GET {path} -> {r.status_code}"
    m = _CSRF_RE.search(r.text)
    assert m, f"no csrf token on {path}"
    return m.group(1)


def do_setup(client, username="admin", password="password123"):
    token = csrf(client, "/setup")
    r = client.post("/setup", data={
        "username": username, "password": password, "confirm": password, "csrf_token": token,
    })
    assert r.status_code == 303 and r.headers["location"] == "/spools", r.text
    return r


def login(client, username, password):
    token = csrf(client, "/login")
    r = client.post("/login", data={"username": username, "password": password, "csrf_token": token})
    assert r.status_code == 303 and r.headers["location"] == "/spools", r.text
    return r


def logout(client):
    token = csrf(client, "/spools")
    return client.post("/logout", data={"csrf_token": token})


def add_user(admin_client, username, password="password123"):
    token = csrf(admin_client, "/admin")
    r = admin_client.post("/admin/users", data={
        "username": username, "password": password, "csrf_token": token,
    })
    assert r.status_code == 303, r.text
    return r


def add_lookup(client, entity, name, **extra) -> int:
    """POST a lookup and return its id as rendered on the list page."""
    token = csrf(client, f"/{entity}")
    r = client.post(f"/{entity}", data={"name": name, "csrf_token": token, **extra})
    assert r.status_code == 303, r.text
    html = client.get(f"/{entity}").text
    for row in re.finditer(r'name="name" value="([^"]*)" form="edit-(\d+)"', html):
        if row.group(1) == name:
            return int(row.group(2))
    raise AssertionError(f"{name} not found on /{entity}")


def lookup_names(client, entity) -> list[str]:
    html = client.get(f"/{entity}").text
    return re.findall(r'name="name" value="([^"]*)" form="edit-\d+"', html)


def add_spool(client, mfg_id, mat_id, col_id, weight=1000, qty=1, sku=None) -> int:
    token = csrf(client, "/spools/new")
    data = {
        "manufacturer_id": mfg_id, "material_type_id": mat_id, "color_id": col_id,
        "weight": weight, "qty": qty, "csrf_token": token,
    }
    if sku:
        data["sku"] = sku
    r = client.post("/spools", data=data)
    assert r.status_code == 303 and r.headers["location"] == "/spools", r.text
    ids = spool_ids(client)
    assert ids, "spool not created"
    return ids[-1]


def spool_ids(client) -> list[int]:
    html = client.get("/spools").text
    return sorted({int(x) for x in re.findall(r'/spools/(\d+)/edit', html)})


def spool_qty(client, spool_id) -> int:
    """Read qty from the edit form (`<input type="number" name="qty" ... value="N">`)."""
    r = client.get(f"/spools/{spool_id}/edit")
    assert r.status_code == 200, f"edit page -> {r.status_code}"
    m = re.search(r'name="qty"[^>]*value="(\d+)"', r.text)
    assert m, "qty not found"
    return int(m.group(1))
