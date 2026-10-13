"""Enterprise auth: users, companies, and bearer sessions on SQLite.

No new dependencies: passwords use stdlib PBKDF2-HMAC-SHA256, sessions are
opaque random tokens (only SHA-256 hashes touch the disk). Companies are
the isolation boundary — every evidence index / job / checkpoint lookup is
scoped to a company slug owned by the calling user.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings

_PBKDF2_ITERATIONS = 200_000


def _db_path() -> str:
    path = getattr(settings, "AUTH_DB_PATH", "./data/auth.sqlite")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
        "pw_hash TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS companies ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL, "
        "owner_id TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
        "created_at TEXT NOT NULL, expires_at TEXT NOT NULL)"
    )
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# -- passwords ------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iters),
        )
        return secrets.compare_digest(digest.hex(), hash_hex)
    except Exception:
        return False


def validate_username(username: str) -> str:
    clean = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,32}", clean):
        raise ValueError("Username must be 3-32 chars: letters, digits, _ or -.")
    return clean


def validate_password(password: str) -> str:
    if len(password or "") < 6:
        raise ValueError("Password must be at least 6 characters.")
    return password


# -- users ----------------------------------------------------------------


def create_user(username: str, password: str) -> dict[str, Any]:
    username = validate_username(username)
    validate_password(password)
    user = {
        "id": _new_id("usr"),
        "username": username,
        "pw_hash": hash_password(password),
        "created_at": _now(),
    }
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO users (id, username, pw_hash, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user["id"], user["username"], user["pw_hash"], user["created_at"]),
            )
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{username}' is already taken.")
    return public_user(user)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": user["id"], "username": user["username"]}


def verify_user(username: str, password: str) -> dict[str, Any] | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM users WHERE username=?", ((username or "").strip(),)
        ).fetchone()
    if row is None:
        return None
    user = dict(row)
    if not check_password(password or "", user.get("pw_hash", "")):
        return None
    return public_user(user)


def get_user(user_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return public_user(dict(row)) if row else None


# -- sessions ---------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: str) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    ttl = timedelta(hours=max(1, int(getattr(settings, "AUTH_TOKEN_TTL_HOURS", 168))))
    now = datetime.now(UTC)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (_token_hash(token), user_id, now.isoformat(), (now + ttl).isoformat()),
        )
    return {"token": token, "expires_at": (now + ttl).isoformat()}


def get_session_user(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT s.*, u.username FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash=?",
            (_token_hash(token),),
        ).fetchone()
    if row is None:
        return None
    try:
        expired = datetime.fromisoformat(row["expires_at"]) < datetime.now(UTC)
    except ValueError:
        expired = True
    if expired:
        revoke_session(token)
        return None
    return {"id": row["user_id"], "username": row["username"]}


def revoke_session(token: str | None) -> None:
    if not token:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


# -- companies --------------------------------------------------------------


def _slugify(name: str) -> str:
    # Case-preserving: existing evidence collections use mixed-case slugs.
    base = re.sub(r"[^A-Za-z0-9]+", "-", (name or "").strip()).strip("-")
    return (base or "company")[:48]


def create_company(owner_id: str, name: str) -> dict[str, Any]:
    clean = (name or "").strip()
    if len(clean) < 2 or len(clean) > 80:
        raise ValueError("Company name must be 2-80 characters.")
    base = _slugify(clean)
    with _connect() as conn:
        slug, n = base, 2
        while conn.execute("SELECT 1 FROM companies WHERE slug=?", (slug,)).fetchone():
            slug = f"{base}-{n}"
            n += 1
        company = {
            "id": _new_id("cmp"),
            "name": clean,
            "slug": slug,
            "owner_id": owner_id,
            "created_at": _now(),
        }
        conn.execute(
            "INSERT INTO companies (id, name, slug, owner_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                company["id"],
                company["name"],
                company["slug"],
                company["owner_id"],
                company["created_at"],
            ),
        )
    return public_company(company)


def public_company(company: dict[str, Any]) -> dict[str, Any]:
    return {"id": company["id"], "name": company["name"], "slug": company["slug"]}


def list_companies(owner_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM companies WHERE owner_id=? ORDER BY created_at",
            (owner_id,),
        ).fetchall()
    return [public_company(dict(r)) for r in rows]


def get_company_by_slug(slug: str) -> dict[str, Any] | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM companies WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def user_owns_slug(user_id: str, slug: str) -> bool:
    company = get_company_by_slug(slug)
    return bool(company) and company.get("owner_id") == user_id
