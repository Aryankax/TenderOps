"""Shared auth dependencies: Bearer token (header) or access_token (SSE query)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.services import auth as auth_store

_bearer = HTTPBearer(auto_error=False)


def _extract_token(
    request: Request,
    creds: HTTPAuthorizationCredentials | None,
) -> str | None:
    if creds and creds.scheme.lower() == "bearer" and creds.credentials:
        return creds.credentials
    # EventSource cannot set headers — the stream endpoint accepts a token
    # query param. Browsers never log POST bodies, and GET query leakage is
    # accepted here the same way OAuth2 implicit flow did it.
    return request.query_params.get("access_token")


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    if not getattr(settings, "AUTH_REQUIRED", True):
        return {"id": "local", "username": "local"}
    user = auth_store.get_session_user(_extract_token(request, creds))
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Log in and send a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_company_access(user: dict[str, Any], slug: str) -> str:
    """Company isolation boundary: 403 unless the caller owns this slug."""
    from app.agents.evidence_agent.retrieval import sanitize_company_id

    clean = sanitize_company_id(slug)
    if not auth_store.user_owns_slug(user["id"], clean):
        raise HTTPException(
            status_code=403,
            detail=f"No access to company '{clean}'. Create it first.",
        )
    return clean
