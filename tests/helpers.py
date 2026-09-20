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
