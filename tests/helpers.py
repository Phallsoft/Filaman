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
