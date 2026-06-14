"""Smoke test for admin flows: backup -> reset -> setup -> restore -> verify."""
import re
import sys

import httpx

BASE = "http://127.0.0.1:8000"
RESET_PHRASE = "Yes, Really Reset The Database"


def get_csrf(client: httpx.Client, path: str) -> str:
    r = client.get(f"{BASE}{path}", follow_redirects=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    assert m, f"no csrf on {path} (status {r.status_code})"
    return m.group(1)


def check(name: str, cond: bool, extra: str = ""):
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" ({extra})" if extra else ""))
    if not cond:
        sys.exit(1)


c = httpx.Client()

# login
csrf = get_csrf(c, "/login")
r = c.post(f"{BASE}/login", data={"username": "admin", "password": "testpass123", "csrf_token": csrf})
check("login", r.status_code == 303 and r.headers["location"] == "/spools")

# wrong password rejected
csrf = get_csrf(c, "/admin")
r = c.post(f"{BASE}/admin/password", data={
    "current_password": "wrong", "new_password": "newpass123",
    "confirm_password": "newpass123", "csrf_token": csrf})
r2 = c.get(f"{BASE}/admin")
check("wrong current password rejected", "Current password is incorrect" in r2.text)

# backup
r = c.get(f"{BASE}/admin/backup")
backup = r.content
check("backup downloads sqlite file", backup[:16] == b"SQLite format 3\x00", f"{len(backup)} bytes")

# reset with wrong phrase
csrf = get_csrf(c, "/admin")
r = c.post(f"{BASE}/admin/reset", data={"confirm_text": "nope", "csrf_token": csrf})
r2 = c.get(f"{BASE}/admin")
check("reset blocked w/ wrong phrase", "Reset cancelled" in r2.text)

# real reset
csrf = get_csrf(c, "/admin")
r = c.post(f"{BASE}/admin/reset", data={"confirm_text": RESET_PHRASE, "csrf_token": csrf})
check("reset redirects to setup", r.status_code == 303 and r.headers["location"] == "/setup")
r = c.get(f"{BASE}/spools")
check("all traffic redirected to /setup after reset", r.status_code == 303 and r.headers["location"] == "/setup")

# invalid restore file rejected (need a user first)
csrf = get_csrf(c, "/setup")
r = c.post(f"{BASE}/setup", data={"username": "temp", "password": "temppass123", "confirm": "temppass123", "csrf_token": csrf})
check("re-setup after reset", r.status_code == 303)
csrf = get_csrf(c, "/admin")
r = c.post(f"{BASE}/admin/restore", files={"file": ("bad.db", b"not a database")}, data={"csrf_token": csrf})
r2 = c.get(f"{BASE}/admin")
check("invalid restore rejected", "Restore failed" in r2.text and "not a SQLite database" in r2.text)

# real restore
csrf = get_csrf(c, "/admin")
r = c.post(f"{BASE}/admin/restore", files={"file": ("backup.db", backup)}, data={"csrf_token": csrf})
check("restore logs out", r.status_code == 303 and r.headers["location"] == "/login")

# original creds work again, data intact
csrf = get_csrf(c, "/login")
r = c.post(f"{BASE}/login", data={"username": "admin", "password": "testpass123", "csrf_token": csrf})
check("original login restored", r.status_code == 303)
r = c.get(f"{BASE}/spools")
check("spool data restored", "Polymaker" in r.text and "Overture" in r.text)

print("ALL ADMIN SMOKE TESTS PASSED")
