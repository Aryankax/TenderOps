"""Shared HTTP-test helpers: isolated auth DB + logged-in headers."""


def isolate_auth(monkeypatch, tmp_path):
    from app.core import config as config_mod

    monkeypatch.setattr(
        config_mod.settings, "AUTH_DB_PATH", str(tmp_path / "auth.sqlite")
    )


def signup(client, username="tester", password="test1234"):
    r = client.post(
        "/api/auth/signup", json={"username": username, "password": password}
    )
    assert r.status_code == 201, r.text
    return r.json()


def login_headers(client, username="tester", password="test1234"):
    body = signup(client, username, password)
    return {"Authorization": f"Bearer {body['token']}"}, body


def make_company(client, headers, name="Acme"):
    r = client.post("/api/companies", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()
