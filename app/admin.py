from __future__ import annotations

import base64
import hmac
import os
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.responses import HTMLResponse

from .db import get_db_session
from .db_models import DeliveryOutbox
from .tasks import send_delivery

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/admin", tags=["admin"])

STATUS_OPTIONS = [
    "",
    "WAITING_FOR_SITE",
    "CHECKING_SITE",
    "COMPLETED_PENDING_SEND",
    "READY_TO_SEND",
    "READY",
    "FAILED",
    "SENDING",
    "SENT",
]


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")


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
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD is missing")
    if not authorization:
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    _, password = _parse_basic_auth(authorization)
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=403, detail="Forbidden")


def _get_delivery(session: Session, delivery_id: UUID) -> DeliveryOutbox:
    row = session.get(DeliveryOutbox, delivery_id)
    if row is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    return row


def _list_deliveries(
    session: Session,
    status: str | None,
    page: int,
    page_size: int,
) -> tuple[list[DeliveryOutbox], int]:
    filters: Iterable = []
    if status:
        filters = [DeliveryOutbox.status == status]

    count_stmt = select(func.count()).select_from(DeliveryOutbox)
    if status:
        count_stmt = count_stmt.where(*filters)
    total = session.execute(count_stmt).scalar_one()

    stmt = select(DeliveryOutbox).order_by(DeliveryOutbox.created_at.desc())
    if status:
        stmt = stmt.where(*filters)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = session.execute(stmt).scalars().all()
    return rows, total


def _render_row(request: Request, delivery: DeliveryOutbox) -> HTMLResponse:
    return templates.TemplateResponse(
        "partials/delivery_row.html",
        {"request": request, "delivery": delivery, "format_dt": _format_dt},
    )


@router.get("/deliveries", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def deliveries_page(
    request: Request,
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
):
    rows, total = _list_deliveries(session, status, page, page_size)
    has_prev = page > 1
    has_next = page * page_size < total
    return templates.TemplateResponse(
        "admin_deliveries.html",
        {
            "request": request,
            "deliveries": rows,
            "status": status or "",
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_prev": has_prev,
            "has_next": has_next,
            "status_options": STATUS_OPTIONS,
            "format_dt": _format_dt,
        },
    )


@router.post("/deliveries/{delivery_id}/override-url", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def override_url(
    request: Request,
    delivery_id: UUID,
    override_target_url: str = Form(...),
    session: Session = Depends(get_db_session),
):
    row = _get_delivery(session, delivery_id)
    value = override_target_url.strip()
    row.override_target_url = value if value else None
    session.commit()
    session.refresh(row)
    return _render_row(request, row)


@router.post("/deliveries/{delivery_id}/send-now", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def send_now(
    request: Request,
    delivery_id: UUID,
    session: Session = Depends(get_db_session),
):
    _get_delivery(session, delivery_id)
    send_delivery.delay(str(delivery_id))
    row = _get_delivery(session, delivery_id)
    return _render_row(request, row)


@router.post("/deliveries/{delivery_id}/mark-ready", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def mark_ready(
    request: Request,
    delivery_id: UUID,
    session: Session = Depends(get_db_session),
):
    row = _get_delivery(session, delivery_id)
    row.status = "READY_TO_SEND"
    row.last_error = None
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return _render_row(request, row)
