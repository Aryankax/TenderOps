from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.services import auth as auth_store

router = APIRouter(prefix="/api/auth", tags=["Auth"])
_bearer = HTTPBearer(auto_error=False)


class Credentials(BaseModel):
    username: str
    password: str


@router.post(
    "/signup",
    status_code=201,
    summary="Create an account",
    responses={400: {"description": "Invalid name/password or name taken."}},
)
async def signup(creds: Credentials):
    try:
        user = auth_store.create_user(creds.username, creds.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    session = auth_store.create_session(user["id"])
    return {"user": user, **session}


@router.post(
    "/login",
    summary="Log in",
    responses={401: {"description": "Wrong username or password."}},
)
async def login(creds: Credentials):
    user = auth_store.verify_user(creds.username, creds.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    session = auth_store.create_session(user["id"])
    return {"user": user, **session}


@router.post("/logout", summary="Revoke the current token")
async def logout(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    user: dict[str, Any] = Depends(get_current_user),
):
    token = None
    if creds and creds.scheme.lower() == "bearer":
        token = creds.credentials
    auth_store.revoke_session(token)
    return {"ok": True, "user": user}


@router.get("/me", summary="Current user")
async def me(user: dict[str, Any] = Depends(get_current_user)):
    return {"user": user, "companies": auth_store.list_companies(user["id"])}
