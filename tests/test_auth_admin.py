from tests.helpers import do_setup, login, logout


def test_setup_then_login(client):
    assert client.get("/spools").headers["location"] == "/setup"
    do_setup(client, "admin", "password123")
    assert client.get("/spools").status_code == 200
    logout(client)
    assert client.get("/spools").headers["location"] == "/login"
    login(client, "admin", "password123")
    assert client.get("/spools").status_code == 200
