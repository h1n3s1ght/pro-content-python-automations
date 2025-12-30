from __future__ import annotations

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
