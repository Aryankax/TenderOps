"""Auth + company workspace: signup/login, tokens, ownership isolation."""


def _client():
    from fastapi.testclient import TestClient
    import app.main as m

    return TestClient(m.app, raise_server_exceptions=False)


def test_signup_login_me_logout_roundtrip(monkeypatch, tmp_path):
    from helpers import isolate_auth

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    r = c.post("/api/auth/signup",
               json={"username": "alice", "password": "secret123"})
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    assert r.json()["user"]["username"] == "alice"

    # Wrong password rejected.
    assert c.post("/api/auth/login",
                  json={"username": "alice", "password": "nope"}).status_code == 401
    r = c.post("/api/auth/login",
               json={"username": "alice", "password": "secret123"})
    assert r.status_code == 200, r.text

    headers = {"Authorization": f"Bearer {token}"}
    r = c.get("/api/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "alice"

    assert c.post("/api/auth/logout", headers=headers).status_code == 200
    # Revoked token stops working.
    assert c.get("/api/auth/me", headers=headers).status_code == 401


def test_signup_validation_and_duplicates(monkeypatch, tmp_path):
    from helpers import isolate_auth

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    assert c.post("/api/auth/signup",
                  json={"username": "x", "password": "secret123"}).status_code == 400
    assert c.post("/api/auth/signup",
                  json={"username": "bob", "password": "short"}).status_code == 400
    assert c.post("/api/auth/signup",
                  json={"username": "bob", "password": "secret123"}).status_code == 201
    assert c.post("/api/auth/signup",
                  json={"username": "bob", "password": "secret123"}).status_code == 400


def test_passwords_are_hashed(monkeypatch, tmp_path):
    import sqlite3
    from helpers import isolate_auth

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    c.post("/api/auth/signup",
           json={"username": "carol", "password": "secret123"})
    from app.core import config as config_mod

    conn = sqlite3.connect(config_mod.settings.AUTH_DB_PATH)
    stored = conn.execute("SELECT pw_hash FROM users").fetchone()[0]
    assert "secret123" not in stored
    assert stored.startswith("pbkdf2$")


def test_company_crud_and_isolation(monkeypatch, tmp_path):
    from helpers import isolate_auth, login_headers

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    h1, _ = login_headers(c, "owner1")
    h2, _ = login_headers(c, "owner2")

    assert c.get("/api/companies", headers=h1).json() == {"companies": []}
    r = c.post("/api/companies", json={"name": "Acme Corp"}, headers=h1)
    assert r.status_code == 201, r.text
    assert r.json()["slug"] == "Acme-Corp"
    assert c.get("/api/companies", headers=h2).json() == {"companies": []}

    # Cross-owner access denied everywhere.
    assert c.get("/api/companies/Acme-Corp",
                 headers=h2).status_code == 403
    assert c.get("/api/companies/Acme-Corp/documents",
                 headers=h2).status_code == 403
    assert c.get("/api/companies/Acme-Corp", headers=h1).status_code == 200


def test_unauthenticated_api_rejected(monkeypatch, tmp_path):
    from helpers import isolate_auth

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    assert c.get("/api/companies").status_code == 401
    assert c.post("/api/jobs").status_code in (401, 422)
    assert c.get("/api/jobs/nope").status_code in (401, 404)
    assert c.get("/api/events/stream").status_code == 401
