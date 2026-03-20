from __future__ import annotations

import base64
import hmac
import os

from fastapi import Header, HTTPException


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("API_BEARER_TOKEN", "").strip()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


def _parse_basic_auth(authorization: str) -> tuple[str, str]:
    try:
        scheme, encoded = authorization.split(" ", 1)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header", headers={"WWW-Authenticate": "Basic"})
    if scheme.lower() != "basic":
        raise HTTPException(status_code=401, detail="Invalid auth scheme", headers={"WWW-Authenticate": "Basic"})
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid auth encoding", headers={"WWW-Authenticate": "Basic"})
    if ":" not in decoded:
        raise HTTPException(status_code=401, detail="Invalid auth format", headers={"WWW-Authenticate": "Basic"})
    username, password = decoded.split(":", 1)
    return username, password


def require_admin_auth(authorization: str | None = Header(default=None)) -> None:
    admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not admin_password:
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD is missing")
    if not authorization:
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    _, password = _parse_basic_auth(authorization)
    if not hmac.compare_digest(password, admin_password):
        raise HTTPException(status_code=403, detail="Forbidden")
