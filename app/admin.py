from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .auth import require_admin_auth
from .db import get_db_session
from .db_models import DeliveryOutbox, JobCopy, RecentlyDeletedJobCopy
from .copy_store import (
    SOFT_DELETE_HOURS_DEFAULT,
    list_job_copies,
    list_recently_deleted_job_copies,
    soft_delete_job_copy,
)
from .delivery_rerun import parse_rerun_request_from_form, queue_rerun_from_job_id
from .delivery_versions import (
    delivery_client_key,
    list_version_options_for_client,
    resolve_requested_version_job_id,
)
from .tasks import finalize_deleted_job_copy, send_delivery

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/admin", tags=["admin"])

STATUS_OPTIONS = [
    "",
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


def _delivery_version_data(session: Session, delivery: DeliveryOutbox) -> tuple[list[dict], str]:
    client_key = delivery_client_key(
        session,
        job_id=delivery.job_id,
        client_name=delivery.client_name,
    )
    options = list_version_options_for_client(session, client_key=client_key)
    default_job_id = options[0].job_id if options else ""
    return [opt.model_dump(mode="json") for opt in options], default_job_id


def _render_row(
    request: Request,
    session: Session,
    delivery: DeliveryOutbox,
    *,
    rerun_job_id: str | None = None,
) -> HTMLResponse:
    options, default_job_id = _delivery_version_data(session, delivery)
    return templates.TemplateResponse(
        "partials/delivery_row.html",
        {
            "request": request,
            "delivery": delivery,
            "format_dt": _format_dt,
            "version_options_by_delivery_id": {str(delivery.id): options},
            "default_version_by_delivery_id": {str(delivery.id): default_job_id},
            "rerun_job_id_by_delivery_id": {str(delivery.id): rerun_job_id or ""},
        },
    )


def _render_copy_row(request: Request, copy: JobCopy) -> HTMLResponse:
    return templates.TemplateResponse(
        "partials/copy_row.html",
        {"request": request, "copy": copy, "format_dt": _format_dt},
    )


def _render_deleted_copy_row(request: Request, copy: RecentlyDeletedJobCopy) -> HTMLResponse:
    return templates.TemplateResponse(
        "partials/recently_deleted_copy_row.html",
        {"request": request, "copy": copy, "format_dt": _format_dt},
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
    version_options_by_delivery_id: dict[str, list[dict]] = {}
    default_version_by_delivery_id: dict[str, str] = {}
    rerun_job_id_by_delivery_id: dict[str, str] = {}
    for delivery in rows:
        options, default_job_id = _delivery_version_data(session, delivery)
        did = str(delivery.id)
        version_options_by_delivery_id[did] = options
        default_version_by_delivery_id[did] = default_job_id
        rerun_job_id_by_delivery_id[did] = ""

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
            "version_options_by_delivery_id": version_options_by_delivery_id,
            "default_version_by_delivery_id": default_version_by_delivery_id,
            "rerun_job_id_by_delivery_id": rerun_job_id_by_delivery_id,
        },
    )


@router.get("/copies", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def copies_page(
    request: Request,
    client: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    rows, total = list_job_copies(client_substring=(client or "").strip() or None, page=page, page_size=page_size)
    has_prev = page > 1
    has_next = page * page_size < total
    return templates.TemplateResponse(
        "admin_copies.html",
        {
            "request": request,
            "copies": rows,
            "client": client or "",
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_prev": has_prev,
            "has_next": has_next,
            "format_dt": _format_dt,
        },
    )


@router.get("/copies/recently-deleted", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def recently_deleted_copies_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    rows, total = list_recently_deleted_job_copies(page=page, page_size=page_size)
    has_prev = page > 1
    has_next = page * page_size < total
    return templates.TemplateResponse(
        "admin_recently_deleted_copies.html",
        {
            "request": request,
            "copies": rows,
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_prev": has_prev,
            "has_next": has_next,
            "format_dt": _format_dt,
        },
    )


@router.get("/copies/{job_id}", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def copy_view_page(
    request: Request,
    job_id: str,
    session: Session = Depends(get_db_session),
):
    job_id = job_id.strip()
    row = session.execute(select(JobCopy).where(JobCopy.job_id == job_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="copy not found")

    pretty = json.dumps(row.copy_data or {}, ensure_ascii=False, indent=2)
    return templates.TemplateResponse(
        "admin_copy_view.html",
        {
            "request": request,
            "copy": row,
            "copy_json_pretty": pretty,
            "format_dt": _format_dt,
        },
    )


@router.get("/copies/{job_id}/download", dependencies=[Depends(require_admin_auth)])
def copy_download(
    job_id: str,
    session: Session = Depends(get_db_session),
):
    job_id = job_id.strip()
    row = session.execute(select(JobCopy).where(JobCopy.job_id == job_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="copy not found")
    body = json.dumps(row.copy_data or {}, ensure_ascii=False, separators=(",", ":"))
    filename = f"{job_id}.json"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=body, media_type="application/json", headers=headers)


@router.post("/copies/{job_id}/delete", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def copy_delete(
    request: Request,
    job_id: str,
):
    job_id = job_id.strip()
    deleted_id = soft_delete_job_copy(job_id=job_id)
    if deleted_id is None:
        raise HTTPException(status_code=404, detail="copy not found")

    # Schedule the final destroy after the grace period.
    finalize_deleted_job_copy.apply_async(args=[job_id], countdown=int(SOFT_DELETE_HOURS_DEFAULT * 3600))

    # Redirect back to the list.
    return RedirectResponse(url="/admin/copies", status_code=303)


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
    return _render_row(request, session, row)


@router.post("/deliveries/{delivery_id}/send-now", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def send_now(
    request: Request,
    delivery_id: UUID,
    session: Session = Depends(get_db_session),
):
    _get_delivery(session, delivery_id)
    send_delivery.delay(str(delivery_id))
    row = _get_delivery(session, delivery_id)
    return _render_row(request, session, row)


def _parse_replay_flag(value: str | None) -> bool:
    raw = str(value or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


@router.post("/deliveries/{delivery_id}/send-version", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def send_version(
    request: Request,
    delivery_id: UUID,
    version_job_id: str = Form(default=""),
    replay: str = Form(default="0"),
    session: Session = Depends(get_db_session),
):
    row = _get_delivery(session, delivery_id)
    replay_requested = _parse_replay_flag(replay)
    client_key = delivery_client_key(session, job_id=row.job_id, client_name=row.client_name)

    selected_job_id = str(version_job_id or "").strip()
    payload_ref_override: str | None = None
    if selected_job_id:
        exists, belongs = resolve_requested_version_job_id(
            session,
            client_key=client_key,
            version_job_id=selected_job_id,
        )
        if not exists:
            raise HTTPException(status_code=404, detail="selected version not found")
        if not belongs:
            raise HTTPException(status_code=400, detail="selected version does not belong to this client")
        payload_ref_override = f"db:{selected_job_id}"
    else:
        options = list_version_options_for_client(session, client_key=client_key)
        if options:
            payload_ref_override = f"db:{options[0].job_id}"

    send_delivery.delay(str(delivery_id), "pro", replay_requested, payload_ref_override)
    row = _get_delivery(session, delivery_id)
    return _render_row(request, session, row)


@router.post("/deliveries/{delivery_id}/rerun", response_class=HTMLResponse, dependencies=[Depends(require_admin_auth)])
def rerun_delivery(
    request: Request,
    delivery_id: UUID,
    mode: str = Form(default=""),
    specific_instructions: str = Form(default=""),
    new_pages_json: str = Form(default=""),
    manual_source_payload_json: str = Form(default=""),
    session: Session = Depends(get_db_session),
):
    row = _get_delivery(session, delivery_id)
    try:
        rerun_request = parse_rerun_request_from_form(
            mode=mode,
            specific_instructions=specific_instructions,
            new_pages_json=new_pages_json,
            manual_source_payload_json=manual_source_payload_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        new_job_id = queue_rerun_from_job_id(
            row.job_id,
            rerun_request=rerun_request,
            source_delivery_id=str(delivery_id),
            client_name=row.client_name,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc)
            or (
                "missing rerun source payload for this delivery job; "
                "this usually means it was created before source capture was enabled"
            ),
        )
    return _render_row(request, session, row, rerun_job_id=new_job_id)


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
    return _render_row(request, session, row)
