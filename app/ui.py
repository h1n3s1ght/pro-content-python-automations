import time
import json
import os
import logging
from datetime import datetime
from uuid import UUID
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi import Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import get_db_session
from .db_models import DeliveryOutbox
from .delivery_schemas import DeliveryListResponse, DeliveryOutboxSchema, OverrideURLRequest, SendNowResponse
from .storage import (
    list_jobs_with_scores,
    get_status,
    get_progress,
    get_log,
    cancel_queued_job,
    move_job,
    pause_job,
    resume_job,
)
from .copy_store import SOFT_DELETE_HOURS_DEFAULT, soft_delete_job_copy
from .outbox import delivery_outbox_table_name_for_tier, normalize_delivery_tier
from .tasks import finalize_deleted_job_copy, purge_local_payload, run_resume_job, send_delivery
from .s3_upload import head_object_info

router = APIRouter(prefix="/ui", tags=["ui"])
logger = logging.getLogger(__name__)


def _safe_db_location() -> str:
    raw = (os.getenv("DATABASE_URL", "") or "").strip()
    if not raw:
        return "missing"
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    if raw.startswith("postgresql://") and "+psycopg" not in raw:
        raw = "postgresql+psycopg://" + raw[len("postgresql://") :]
    try:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
        port = parsed.port or ""
        dbname = (parsed.path or "").lstrip("/")
        if host and port and dbname:
            return f"{host}:{port}/{dbname}"
        if host and dbname:
            return f"{host}/{dbname}"
        return dbname or host or "unknown"
    except Exception:
        return "unknown"


def _badge(text: str) -> str:
    t = (text or "unknown").lower()
    if t == "completed":
        cls = "badge rounded-pill bg-success-subtle text-success-emphasis border border-success-subtle"
    elif t == "running":
        cls = "badge rounded-pill bg-primary-subtle text-primary-emphasis border border-primary-subtle"
    elif t == "failed":
        cls = "badge rounded-pill bg-danger-subtle text-danger-emphasis border border-danger-subtle"
    elif t == "queued":
        cls = "badge rounded-pill bg-warning-subtle text-warning-emphasis border border-warning-subtle"
    elif t == "paused":
        cls = "badge rounded-pill bg-orange-subtle text-orange-emphasis border border-orange-subtle"
    elif t == "canceled":
        cls = "badge rounded-pill bg-secondary-subtle text-secondary-emphasis border border-secondary-subtle"
    else:
        cls = "badge rounded-pill bg-secondary-subtle text-secondary-emphasis border border-secondary-subtle"
    return f"<span class=\"{cls}\">{text}</span>"


@router.get("/api/queue")
async def queue_data():
    now = time.time()
    twenty_four_hours_ago = now - (24 * 3600)
    capacity = int(os.getenv("CELERY_CONCURRENCY", os.getenv("WEB_CONCURRENCY", "2")) or "2")
    if capacity < 1:
        capacity = 1

    items = []
    status_pairs = []

    jobs_with_scores = await list_jobs_with_scores(500, newest_first=False, min_score=twenty_four_hours_ago, max_score=None)
    for jid, score in jobs_with_scores:
        status = await get_status(jid) or "unknown"
        status_pairs.append((jid, status, score))

    running_slots = 0
    queue_index = 0
    for jid, status, score in status_pairs:
        st = (status or "").lower()
        display_status = status
        is_running_like = st in ("running", "starting")

        if is_running_like:
            running_slots += 1
            if running_slots > capacity:
                display_status = "queued"
                st = "queued"
                queue_index += 1
                queue_pos = queue_index
            else:
                queue_pos = None
        elif st in ("queued", "paused"):
            queue_index += 1
            queue_pos = queue_index
        else:
            queue_pos = None

        prog = await get_progress(jid)
        items.append(
            {
                "job_id": jid,
                "status": status,
                "display_status": display_status,
                "created_at": int(score) if score is not None else None,
                "queue_position": queue_pos,
                "stage": prog.get("stage", ""),
                "pages_total": prog.get("pages_total", ""),
                "pages_done": prog.get("pages_done", ""),
                "pages_failed": prog.get("pages_failed", ""),
                "current": prog.get("current", ""),
                "can_cancel": st in ("queued", "paused", "running", "starting"),
                "can_move": st in ("queued", "paused"),
                "can_pause": st in ("queued", "running", "starting"),
                "can_resume": st == "paused",
            }
        )
    return JSONResponse(items)


@router.get("/api/job/{job_id}")
async def job_data(job_id: str):
    status = await get_status(job_id) or "unknown"
    prog = await get_progress(job_id)
    logs = await get_log(job_id, 300)
    return JSONResponse({"job_id": job_id, "status": status, "progress": prog, "logs": logs})


@router.get("/api/job/{job_id}/delivery-trace")
async def delivery_trace(job_id: str, session: Session = Depends(get_db_session)):
    """
    Debug helper to understand why a completed job did/did not create a delivery_outbox row.
    Avoids returning secrets; only returns safe, high-signal fields.
    """
    status = await get_status(job_id) or "unknown"
    prog = await get_progress(job_id)
    logs = await get_log(job_id, 500)

    needles = (
        "sitemap_uploaded",
        "sitemap_upload_failed",
        "sitemap_saved_db",
        "sitemap_db_save_failed",
        "copy_uploaded",
        "copy_upload_failed",
        "payload_stored",
        "payload_store_failed",
        "outbox_",
        "preview_url_",
    )
    interesting_logs = [line for line in (logs or []) if any(n in line for n in needles)]

    inferred_s3_key = ""
    for line in reversed(logs or []):
        if "copy_uploaded:" in line:
            inferred_s3_key = line.split("copy_uploaded:", 1)[1].strip()
            break
        if "payload_stored:" in line:
            inferred_s3_key = line.split("payload_stored:", 1)[1].strip()
            break

    outbox_row = session.execute(select(DeliveryOutbox).where(DeliveryOutbox.job_id == job_id)).scalars().first()
    outbox = DeliveryOutboxSchema.model_validate(outbox_row).model_dump(mode="json") if outbox_row else None

    s3_key = inferred_s3_key
    if outbox and outbox.get("payload_s3_key"):
        s3_key = str(outbox.get("payload_s3_key") or "").strip()

    s3_head = head_object_info(s3_key) if s3_key else None

    return JSONResponse(
        {
            "job_id": job_id,
            "job_status": status,
            "progress": prog,
            "db": {"location": _safe_db_location()},
            "logs_interesting": interesting_logs[-200:],
            "inferred": {"payload_s3_key": inferred_s3_key},
            "outbox": outbox,
            "s3_head": s3_head,
        }
    )


@router.post("/job/{job_id}/cancel")
async def cancel_job(job_id: str):
    ok = await cancel_queued_job(job_id)
    return JSONResponse({"ok": ok})


@router.post("/job/{job_id}/move")
async def move_job_endpoint(job_id: str, dir: str = Query(default="bottom")):
    ok = await move_job(job_id, dir)
    return JSONResponse({"ok": ok})


@router.post("/job/{job_id}/pause")
async def pause_job_endpoint(job_id: str):
    ok = await pause_job(job_id)
    return JSONResponse({"ok": ok})


@router.post("/job/{job_id}/resume")
async def resume_job_endpoint(job_id: str):
    ok = await resume_job(job_id)
    if ok:
        run_resume_job.delay(job_id)
    return JSONResponse({"ok": ok})


_DELIVERY_QUERY_COLUMNS = (
    "id",
    "job_id",
    "client_name",
    "payload_s3_key",
    "default_target_url",
    "override_target_url",
    "preview_url",
    "status",
    "scheduled_for",
    "attempt_count",
    "site_check_attempts",
    "site_check_next_at",
    "last_error",
    "created_at",
    "updated_at",
    "sent_at",
)

_DELIVERY_TEXT_DEFAULTS = {
    "job_id": "''",
    "client_name": "''",
    "payload_s3_key": "''",
    "default_target_url": "''",
    "status": "''",
}

_DELIVERY_TIME_DEFAULTS = {
    "created_at": "NOW()",
    "updated_at": "NOW()",
}


def _website_tier_label(tier: str) -> str:
    return "Express" if normalize_delivery_tier(tier) == "express" else "Pro"


def _table_columns(session: Session, table_name: str) -> set[str]:
    stmt = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = :table_name
        """
    )
    return {str(row[0]) for row in session.execute(stmt, {"table_name": table_name}).all()}


def _column_projection(column: str, columns: set[str]) -> str:
    if column in columns:
        if column in ("attempt_count", "site_check_attempts"):
            return f"COALESCE({column}, 0) AS {column}"
        return f"{column} AS {column}"
    if column in ("attempt_count", "site_check_attempts"):
        return f"0 AS {column}"
    if column in _DELIVERY_TEXT_DEFAULTS:
        return f"{_DELIVERY_TEXT_DEFAULTS[column]} AS {column}"
    if column in _DELIVERY_TIME_DEFAULTS:
        return f"{_DELIVERY_TIME_DEFAULTS[column]} AS {column}"
    if column == "id":
        return "'00000000-0000-0000-0000-000000000000'::uuid AS id"
    return f"NULL AS {column}"


def _delivery_select_sql(table_name: str, columns: set[str], tier_label: str) -> str:
    projected = [_column_projection(column, columns) for column in _DELIVERY_QUERY_COLUMNS]
    projected.append(f"'{tier_label}' AS website_tier")
    return f"SELECT {', '.join(projected)} FROM {table_name}"


def _fetch_delivery_row(session: Session, delivery_id: UUID, *, tier: str) -> dict:
    tier_name = normalize_delivery_tier(tier)
    table_name = delivery_outbox_table_name_for_tier(tier_name)
    columns = _table_columns(session, table_name)
    if not columns:
        raise HTTPException(status_code=404, detail="delivery not found")

    select_sql = _delivery_select_sql(table_name, columns, _website_tier_label(tier_name))
    stmt = text(f"SELECT * FROM ({select_sql}) AS deliveries WHERE id = :delivery_id")
    row = session.execute(stmt, {"delivery_id": delivery_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    return dict(row)


def _update_override_url(session: Session, delivery_id: UUID, override_target_url: str, *, tier: str) -> None:
    tier_name = normalize_delivery_tier(tier)
    table_name = delivery_outbox_table_name_for_tier(tier_name)
    columns = _table_columns(session, table_name)
    if not columns or "override_target_url" not in columns:
        raise HTTPException(status_code=404, detail="delivery not found")

    set_clauses = ["override_target_url = :override_target_url"]
    if "updated_at" in columns:
        set_clauses.append("updated_at = NOW()")

    stmt = text(f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE id = :delivery_id")
    result = session.execute(
        stmt,
        {
            "delivery_id": delivery_id,
            "override_target_url": override_target_url,
        },
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="delivery not found")


def _deliveries_redirect(
    *,
    flash_success: str | None = None,
    flash_error: str | None = None,
    admin_actions: bool = False,
) -> RedirectResponse:
    params = {}
    if flash_success:
        params["flash_success"] = flash_success
    if flash_error:
        params["flash_error"] = flash_error
    if admin_actions:
        params["adminActions"] = "true"

    url = "/ui/deliveries"
    if params:
        url = f"{url}?{urlencode(params)}"
    return RedirectResponse(url=url, status_code=303)


@router.get("/api/deliveries", response_model=DeliveryListResponse)
def list_deliveries(
    status: str | None = Query(default=None),
    client: str | None = Query(default=None),
    tier: str | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
):
    logger.info(
        "ui_list_deliveries_start status=%s client=%s tier=%s created_from=%s created_to=%s page=%s page_size=%s db=%s",
        status,
        client,
        tier,
        created_from.isoformat() if created_from else None,
        created_to.isoformat() if created_to else None,
        page,
        page_size,
        _safe_db_location(),
    )
    pro_table = delivery_outbox_table_name_for_tier("pro")
    express_table = delivery_outbox_table_name_for_tier("express")
    pro_cols = _table_columns(session, pro_table)
    express_cols = _table_columns(session, express_table)

    if not pro_cols:
        raise HTTPException(status_code=500, detail="delivery_outbox table not available")

    # /ui/deliveries now unions Pro + Express outboxes into one result set.
    union_parts = [
        _delivery_select_sql(pro_table, pro_cols, "Pro"),
    ]
    if express_cols:
        union_parts.append(_delivery_select_sql(express_table, express_cols, "Express"))

    union_sql = " UNION ALL ".join(union_parts)
    where_clauses = []
    params: dict[str, object] = {
        "offset": (page - 1) * page_size,
        "limit": page_size,
    }
    if status:
        where_clauses.append("status = :status")
        params["status"] = status
    if client:
        where_clauses.append("client_name ILIKE :client")
        params["client"] = f"%{client}%"
    tier_name = (tier or "").strip().lower()
    if tier_name in ("pro", "express"):
        where_clauses.append("website_tier = :website_tier")
        params["website_tier"] = "Express" if tier_name == "express" else "Pro"
    if created_from:
        where_clauses.append("created_at >= :created_from")
        params["created_from"] = created_from
    if created_to:
        where_clauses.append("created_at <= :created_to")
        params["created_to"] = created_to

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    count_stmt = text(f"SELECT COUNT(*) FROM ({union_sql}) AS deliveries {where_sql}")
    total = int(session.execute(count_stmt, params).scalar_one() or 0)

    list_stmt = text(
        f"""
        SELECT *
        FROM ({union_sql}) AS deliveries
        {where_sql}
        ORDER BY created_at DESC NULLS LAST
        OFFSET :offset
        LIMIT :limit
        """
    )
    items = session.execute(list_stmt, params).mappings().all()

    logger.info(
        "ui_list_deliveries_ok total=%s returned=%s",
        int(total or 0),
        len(items),
    )
    return DeliveryListResponse(
        items=[DeliveryOutboxSchema.model_validate(dict(item)) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        status_filter=status,
    )


@router.post("/deliveries/{delivery_id}/override-url", response_model=DeliveryOutboxSchema)
def set_override_url(
    delivery_id: UUID,
    payload: OverrideURLRequest,
    tier: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
):
    tier_name = normalize_delivery_tier(tier)
    _update_override_url(session, delivery_id, payload.override_target_url, tier=tier_name)
    session.commit()
    row = _fetch_delivery_row(session, delivery_id, tier=tier_name)
    return DeliveryOutboxSchema.model_validate(row)


@router.post("/deliveries/{delivery_id}/send-now", response_model=SendNowResponse)
def send_now(
    delivery_id: UUID,
    tier: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
):
    tier_name = normalize_delivery_tier(tier)
    _fetch_delivery_row(session, delivery_id, tier=tier_name)
    async_result = send_delivery.delay(str(delivery_id), tier_name)
    return SendNowResponse(ok=True, task_id=async_result.id)


@router.post("/deliveries/{delivery_id}/delete")
def delete_failed_delivery(
    delivery_id: UUID,
    delete_copy: bool = Query(default=False),
    tier: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
):
    tier_name = normalize_delivery_tier(tier)
    table_name = delivery_outbox_table_name_for_tier(tier_name)
    row = _fetch_delivery_row(session, delivery_id, tier=tier_name)
    if str(row.get("status") or "").upper() != "FAILED":
        raise HTTPException(status_code=400, detail="only FAILED deliveries can be deleted")

    job_id = str(row.get("job_id") or "").strip()
    result = session.execute(text(f"DELETE FROM {table_name} WHERE id = :delivery_id"), {"delivery_id": delivery_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="delivery not found")
    session.commit()

    # Optionally delete the stored copy payload (Postgres soft-delete -> 48h grace -> permanent destroy).
    # NOTE: /ui routes are currently unauthenticated; be mindful if this service is public.
    if delete_copy and job_id:
        try:
            deleted_id = soft_delete_job_copy(job_id=job_id)
            if deleted_id:
                finalize_deleted_job_copy.apply_async(args=[job_id], countdown=int(SOFT_DELETE_HOURS_DEFAULT * 3600))
                logger.info(
                    "delivery_delete_soft_deleted_copy delivery_id=%s tier=%s job_id=%s deleted_id=%s grace_hours=%s",
                    str(delivery_id),
                    tier_name,
                    job_id,
                    str(deleted_id),
                    SOFT_DELETE_HOURS_DEFAULT,
                )
        except Exception as exc:
            # Don't block deleting the delivery row if copy cleanup fails.
            logger.warning(
                "delivery_delete_soft_delete_copy_failed delivery_id=%s tier=%s job_id=%s err=%s",
                str(delivery_id),
                tier_name,
                job_id,
                exc,
            )

    # Keep payload files around briefly for troubleshooting; purge a week after deletion.
    # If delete_copy=true, finalize_deleted_job_copy will also purge the disk file earlier.
    purge_after_seconds = 7 * 24 * 60 * 60
    if job_id:
        purge_local_payload.apply_async(args=[job_id], countdown=purge_after_seconds)
    logger.info(
        "delivery_deleted delivery_id=%s tier=%s job_id=%s purge_in_days=7",
        str(delivery_id),
        tier_name,
        job_id,
    )
    return JSONResponse({"ok": True})


@router.post("/deliveries/remove")
def remove_delivery(
    delivery_id: UUID = Form(...),
    tier: str = Form(default="pro"),
    confirm_name: str = Form(...),
    admin_actions: str = Form(default="false"),
    session: Session = Depends(get_db_session),
):
    tier_name = normalize_delivery_tier(tier)
    table_name = delivery_outbox_table_name_for_tier(tier_name)
    keep_admin_actions = admin_actions == "true"

    try:
        row = _fetch_delivery_row(session, delivery_id, tier=tier_name)
    except HTTPException:
        return _deliveries_redirect(
            flash_error="Delivery not found. Nothing was removed.",
            admin_actions=keep_admin_actions,
        )

    client_name = str(row.get("client_name") or "")
    if confirm_name != client_name:
        return _deliveries_redirect(
            flash_error="Confirmation text did not match exactly. Delivery was not removed.",
            admin_actions=keep_admin_actions,
        )

    try:
        result = session.execute(text(f"DELETE FROM {table_name} WHERE id = :delivery_id"), {"delivery_id": delivery_id})
        if result.rowcount == 0:
            session.rollback()
            return _deliveries_redirect(
                flash_error="Delivery not found. Nothing was removed.",
                admin_actions=keep_admin_actions,
            )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception(
            "delivery_remove_failed delivery_id=%s tier=%s client=%s err=%s",
            str(delivery_id),
            tier_name,
            client_name,
            exc,
        )
        return _deliveries_redirect(
            flash_error="Failed to remove delivery due to a database error.",
            admin_actions=keep_admin_actions,
        )

    return _deliveries_redirect(
        flash_success=f"Removed delivery for {client_name}.",
        admin_actions=keep_admin_actions,
    )


@router.get("/deliveries", response_class=HTMLResponse)
async def deliveries_page():
    html = """
    <html>
      <head>
        <title>Deliveries</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
        <style>
          .admin-action {
            display: none !important;
          }
          body.admin-actions-enabled th.admin-action,
          body.admin-actions-enabled td.admin-action {
            display: table-cell !important;
          }
          body.admin-actions-enabled button.admin-action,
          body.admin-actions-enabled a.admin-action {
            display: inline-flex !important;
          }
          .deliveries-flash-alert {
            position: relative;
            padding-right: 2.75rem;
          }
          .deliveries-flash-alert .btn-close {
            position: absolute;
            top: 50%;
            right: 0.75rem;
            transform: translateY(-50%);
            margin: 0;
            padding: 0.5rem;
          }
          .sort-trigger {
            border: 0;
            background: transparent;
            padding: 0;
            font: inherit;
            font-weight: 600;
            color: inherit;
            cursor: pointer;
          }
          .sort-indicator {
            font-size: 0.85rem;
            color: var(--bs-secondary-color);
            margin-left: 0.2rem;
          }
        </style>
      </head>
      <body class="bg-light">
        <div class="container py-4">
          <div class="d-flex flex-wrap align-items-center justify-content-between mb-3">
            <div>
              <h2 class="mb-0">Deliveries</h2>
              <div class="text-muted small">/ui/deliveries</div>
            </div>
            <div class="d-flex align-items-center gap-2">
              <a href="/ui/queue" class="btn btn-outline-primary btn-sm">Job Queue</a>
              <button id="refreshBtn" class="btn btn-outline-secondary btn-sm">Refresh</button>
              <div class="text-muted small" id="lastUpdated"></div>
            </div>
          </div>

	          <div class="row g-2 mb-3">
	            <div class="col-sm-4">
	              <input id="clientFilter" class="form-control form-control-sm" placeholder="Filter by client name" />
	            </div>
	            <div class="col-sm-2">
	              <select id="statusFilter" class="form-select form-select-sm">
	                <option value="">All statuses</option>
	                <option>WAITING_FOR_SITE</option>
	                <option>CHECKING_SITE</option>
	                <option>READY_TO_SEND</option>
	                <option>COMPLETED_PENDING_SEND</option>
	                <option>FAILED</option>
	                <option>SENDING</option>
	                <option>SENT</option>
	              </select>
	            </div>
	            <div class="col-sm-2">
	              <select id="tierFilter" class="form-select form-select-sm">
	                <option value="">All tiers</option>
	                <option value="pro">Pro</option>
	                <option value="express">Express</option>
	              </select>
	            </div>
	            <div class="col-sm-2">
	              <input id="createdFromFilter" type="datetime-local" class="form-control form-control-sm" />
	            </div>
	            <div class="col-sm-2">
	              <input id="createdToFilter" type="datetime-local" class="form-control form-control-sm" />
	            </div>
	            <div class="col-sm-2">
	              <button id="applyFilters" class="btn btn-sm btn-primary w-100">Apply</button>
	            </div>
	          </div>
	          <div id="flashMessage"></div>

	          <div class="table-responsive">
	            <table class="table table-sm align-middle table-hover bg-white shadow-sm">
              <thead class="table-light">
                <tr>
	                  <th scope="col">
	                    <button type="button" class="sort-trigger" data-sort-key="client_name">Client<span class="sort-indicator" id="sort-indicator-client_name">↕</span></button>
	                  </th>
	                  <th scope="col">Job ID</th>
	                  <th scope="col">
	                    <button type="button" class="sort-trigger" data-sort-key="website_tier">Website Tier<span class="sort-indicator" id="sort-indicator-website_tier">↕</span></button>
	                  </th>
	                  <th scope="col">
	                    <button type="button" class="sort-trigger" data-sort-key="status">Status<span class="sort-indicator" id="sort-indicator-status">↕</span></button>
	                  </th>
                  <th scope="col">Created</th>
	                  <th scope="col" class="admin-action">Delivery URL</th>
                  <th scope="col" class="admin-action">Actions</th>
                </tr>
              </thead>
              <tbody id="deliveriesBody">
                <tr><td colspan="7" class="text-muted">Loading…</td></tr>
	              </tbody>
	            </table>
	          </div>
	        </div>
	        <div class="modal fade" id="removeDeliveryModal" tabindex="-1" aria-labelledby="removeDeliveryModalTitle" aria-hidden="true">
	          <div class="modal-dialog modal-dialog-centered">
	            <div class="modal-content">
	              <div class="modal-header">
	                <h5 class="modal-title" id="removeDeliveryModalTitle">Remove Delivery</h5>
	                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
	              </div>
		              <form id="removeDeliveryForm" method="POST" action="/ui/deliveries/remove">
		                <div class="modal-body">
		                  <p class="mb-2">Are you sure you wish to remove this?</p>
		                  <p class="mb-3">Client: <strong id="removeDeliveryClientName"></strong></p>
		                  <input type="hidden" name="delivery_id" id="removeDeliveryId" />
		                  <input type="hidden" name="tier" id="removeDeliveryTier" />
		                  <input type="hidden" name="admin_actions" id="removeDeliveryAdminActions" value="false" />
		                  <input type="hidden" id="removeExpectedClientName" />
	                  <div class="mb-3">
	                    <label class="form-label" for="removeConfirmName">Type the client name to confirm</label>
	                    <input type="text" class="form-control" name="confirm_name" id="removeConfirmName" autocomplete="off" required />
	                  </div>
	                  <div id="removeConfirmError" class="alert alert-danger py-2 mb-0 d-none"></div>
	                </div>
	                <div class="modal-footer">
	                  <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel Action</button>
	                  <button type="submit" class="btn btn-danger" id="removeConfirmBtn" disabled>I’m sure</button>
	                </div>
	              </form>
	            </div>
	          </div>
	        </div>
	        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
	        <script>
	          const body = document.getElementById("deliveriesBody");
	          const refreshBtn = document.getElementById("refreshBtn");
	          const lastUpdated = document.getElementById("lastUpdated");
	          const clientFilter = document.getElementById("clientFilter");
	          const statusFilter = document.getElementById("statusFilter");
	          const tierFilter = document.getElementById("tierFilter");
	          const createdFromFilter = document.getElementById("createdFromFilter");
	          const createdToFilter = document.getElementById("createdToFilter");
	          const applyFilters = document.getElementById("applyFilters");
	          const sortButtons = Array.from(document.querySelectorAll(".sort-trigger[data-sort-key]"));
	          const flashMessage = document.getElementById("flashMessage");
	          const removeModalEl = document.getElementById("removeDeliveryModal");
	          const removeForm = document.getElementById("removeDeliveryForm");
	          const removeDeliveryId = document.getElementById("removeDeliveryId");
	          const removeDeliveryTier = document.getElementById("removeDeliveryTier");
	          const removeDeliveryAdminActions = document.getElementById("removeDeliveryAdminActions");
	          const removeExpectedClientName = document.getElementById("removeExpectedClientName");
	          const removeDeliveryClientName = document.getElementById("removeDeliveryClientName");
	          const removeConfirmName = document.getElementById("removeConfirmName");
	          const removeConfirmBtn = document.getElementById("removeConfirmBtn");
	          const removeConfirmError = document.getElementById("removeConfirmError");
	          const removeModal = new bootstrap.Modal(removeModalEl);
	          const adminParams = new URLSearchParams(window.location.search);
	          const isAdminActions = adminParams.get("adminActions") === "true" || adminParams.get("adminAction") === "true";
	          const FILTER_COOKIE = "ui_deliveries_filters_v1";
	          const FILTER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;
	          let activeSortKey = null;
	          let activeSortDir = "asc";
	          let rawItems = [];

	          if (isAdminActions) {
	            document.body.classList.add("admin-actions-enabled");
	          }
	          removeDeliveryAdminActions.value = isAdminActions ? "true" : "false";

	          function escapeHtml(value){
	            return String(value || "")
	              .replace(/&/g, "&amp;")
	              .replace(/</g, "&lt;")
	              .replace(/>/g, "&gt;")
	              .replace(/"/g, "&quot;")
	              .replace(/'/g, "&#39;");
	          }

	          function formatDate(ts){
	            if (!ts) return "";
	            try {
	              return new Date(ts).toLocaleString();
	            } catch (_) {
	              return "";
	            }
	          }

	          function toDateTimeLocalValue(date){
	            const d = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
	            return d.toISOString().slice(0, 16);
	          }

	          function parseDateTimeLocalToISO(value){
	            if (!value) return "";
	            const d = new Date(value);
	            return Number.isNaN(d.getTime()) ? "" : d.toISOString();
	          }

	          function defaultDateRange(){
	            const now = new Date();
	            const from = new Date(now.getTime() - (60 * 24 * 60 * 60 * 1000));
	            return {
	              created_from: toDateTimeLocalValue(from),
	              created_to: toDateTimeLocalValue(now),
	            };
	          }

	          function getCookieValue(name){
	            const parts = document.cookie ? document.cookie.split("; ") : [];
	            for (const part of parts){
	              if (part.startsWith(`${name}=`)){
	                return part.slice(name.length + 1);
	              }
	            }
	            return "";
	          }

	          function readFilterPrefs(){
	            const raw = getCookieValue(FILTER_COOKIE);
	            if (!raw) return null;
	            try {
	              const parsed = JSON.parse(decodeURIComponent(raw));
	              if (!parsed || typeof parsed !== "object") return null;
	              return parsed;
	            } catch (_) {
	              return null;
	            }
	          }

	          function writeFilterPrefs(){
	            const payload = {
	              client: (clientFilter.value || "").trim(),
	              status: (statusFilter.value || "").trim(),
	              tier: (tierFilter.value || "").trim(),
	              created_from: (createdFromFilter.value || "").trim(),
	              created_to: (createdToFilter.value || "").trim(),
	            };
	            document.cookie = `${FILTER_COOKIE}=${encodeURIComponent(JSON.stringify(payload))}; Max-Age=${FILTER_COOKIE_MAX_AGE}; Path=/; SameSite=Lax`;
	          }

	          function restoreFilterPrefs(){
	            const saved = readFilterPrefs();
	            const defaults = defaultDateRange();
	            clientFilter.value = saved?.client || "";
	            statusFilter.value = saved?.status || "";
	            tierFilter.value = saved?.tier || "";
	            createdFromFilter.value = saved?.created_from || defaults.created_from;
	            createdToFilter.value = saved?.created_to || defaults.created_to;
	          }

	          function normalizeSortValue(item, key){
	            return String(item?.[key] || "").toLocaleLowerCase();
	          }

	          function sortItems(items){
	            if (!activeSortKey) return [...items];
	            const direction = activeSortDir === "desc" ? -1 : 1;
	            return [...items].sort((a, b) => {
	              const aVal = normalizeSortValue(a, activeSortKey);
	              const bVal = normalizeSortValue(b, activeSortKey);
	              if (aVal === bVal) return 0;
	              return aVal > bVal ? direction : -direction;
	            });
	          }

	          function updateSortIndicators(){
	            for (const btn of sortButtons){
	              const key = btn.dataset.sortKey;
	              const indicator = document.getElementById(`sort-indicator-${key}`);
	              if (!indicator) continue;
	              if (key !== activeSortKey){
	                indicator.textContent = "↕";
	              } else {
	                indicator.textContent = activeSortDir === "asc" ? "↑" : "↓";
	              }
	            }
	          }

	          function renderFlashFromQuery(){
	            const params = new URLSearchParams(window.location.search);
	            const success = (params.get("flash_success") || "").trim();
	            const error = (params.get("flash_error") || "").trim();
	            if (!success && !error) return;
	            const message = error || success;
	            const cls = error ? "alert-danger" : "alert-success";
	            flashMessage.innerHTML = `
	              <div class="alert ${cls} alert-dismissible fade show py-2 deliveries-flash-alert" role="alert">
	                ${escapeHtml(message)}
	                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
	              </div>
	            `;
	            params.delete("flash_success");
	            params.delete("flash_error");
	            const nextQuery = params.toString();
	            const nextUrl = nextQuery ? `${window.location.pathname}?${nextQuery}` : window.location.pathname;
	            window.history.replaceState({}, "", nextUrl);
	          }

	          function renderRows(items){
	            if (!items.length){
	              body.innerHTML = `<tr><td colspan="7" class="text-muted">No deliveries found.</td></tr>`;
	              return;
	            }
	            body.innerHTML = items.map(item => {
	              const tier = String(item.website_tier || "Pro").toLowerCase() === "express" ? "express" : "pro";
	              const rowKey = `${tier}-${item.id}`;
	              const encodedClientName = encodeURIComponent(item.client_name || "").replace(/'/g, "%27");
	              const urlValue = item.override_target_url || "";
	              const placeholder = item.default_target_url || "";
	              const canDelete = String(item.status || "").toUpperCase() === "FAILED";
	              const deleteBtn = canDelete
	                ? `<button class="btn btn-sm btn-outline-danger ms-2 admin-action" onclick="deleteDelivery('${item.id}', '${tier}')">Delete</button>`
	                : ``;
	              const removeBtn = `<button class="btn btn-sm btn-danger ms-2 admin-action" onclick="openRemoveModal('${item.id}', '${tier}', '${encodedClientName}')">Remove</button>`;
	              return `
	                <tr>
	                  <td>${item.client_name || ""}</td>
	                  <td class="text-monospace small">${item.job_id || ""}</td>
	                  <td>${item.website_tier || "Pro"}</td>
	                  <td>${item.status || ""}</td>
	                  <td class="text-muted small">${formatDate(item.created_at)}</td>
	                  <td class="admin-action">
	                    <input id="delivery-url-${rowKey}" class="form-control form-control-sm admin-action" placeholder="${placeholder}" value="${urlValue}" />
	                  </td>
	                  <td class="text-nowrap admin-action">
	                    <button class="btn btn-sm btn-primary admin-action" onclick="sendNow('${item.id}', '${tier}')">Send</button>
	                    ${deleteBtn}
	                    ${removeBtn}
	                  </td>
	                </tr>
	              `;
	            }).join("");
	          }

	          async function apiJSON(url, opts={}){
	            const res = await fetch(url, opts);
	            if (!res.ok) throw new Error(`HTTP ${res.status}`);
	            return await res.json();
	          }

	          async function loadDeliveries(){
	            body.innerHTML = `<tr><td colspan="7" class="text-muted">Loading…</td></tr>`;
	            const params = new URLSearchParams();
	            const client = (clientFilter.value || "").trim();
	            const status = (statusFilter.value || "").trim();
	            const tier = (tierFilter.value || "").trim();
	            const createdFromISO = parseDateTimeLocalToISO(createdFromFilter.value);
	            const createdToISO = parseDateTimeLocalToISO(createdToFilter.value);
	            if (client) params.set("client", client);
	            if (status) params.set("status", status);
	            if (tier) params.set("tier", tier);
	            if (createdFromISO) params.set("created_from", createdFromISO);
	            if (createdToISO) params.set("created_to", createdToISO);
	            writeFilterPrefs();

	            let data;
	            try {
	              data = await apiJSON(`/ui/api/deliveries?${params.toString()}`);
	            } catch (_) {
	              body.innerHTML = `<tr><td colspan="7" class="text-danger">Failed to load deliveries.</td></tr>`;
	              return;
	            }

	            rawItems = data.items || [];
	            updateSortIndicators();
	            renderRows(sortItems(rawItems));
	            lastUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
	          }

	          async function sendNow(id, tier){
	            const rowKey = `${tier}-${id}`;
	            const input = document.getElementById(`delivery-url-${rowKey}`);
	            const url = (input ? input.value : "").trim();
	            if (!url){
	              alert("Please enter a Delivery URL first.");
	              return;
	            }
	            const qs = `?tier=${encodeURIComponent(tier)}`;
	            try {
	              await apiJSON(`/ui/deliveries/${id}/override-url${qs}`, {
	                method: "POST",
	                headers: {"Content-Type": "application/json"},
	                body: JSON.stringify({override_target_url: url}),
	              });
	              await apiJSON(`/ui/deliveries/${id}/send-now${qs}`, { method: "POST" });
	              await loadDeliveries();
	            } catch (_) {
	              alert("Failed to send. Check server logs.");
	            }
	          }

	          async function deleteDelivery(id, tier){
	            const ok = confirm("Delete this FAILED delivery? It will be removed from the list.");
	            if (!ok) return;
	            try {
	              const alsoDeleteCopy = confirm("Also delete the stored job copy payload? It will move to Recently Deleted for 48 hours, then be destroyed.");
	              const base = `tier=${encodeURIComponent(tier)}`;
	              const qs = alsoDeleteCopy ? `?${base}&delete_copy=1` : `?${base}`;
	              await apiJSON(`/ui/deliveries/${id}/delete${qs}`, { method: "POST" });
	              await loadDeliveries();
	            } catch (_) {
	              alert("Failed to delete. Check server logs.");
	            }
	          }

	          function validateRemoveModalInput(){
	            const expected = removeExpectedClientName.value || "";
	            const typed = removeConfirmName.value || "";
	            const matches = typed === expected;
	            removeConfirmBtn.disabled = !matches;
	            if (typed && !matches){
	              removeConfirmError.textContent = "Typed client name must exactly match.";
	              removeConfirmError.classList.remove("d-none");
	            } else {
	              removeConfirmError.classList.add("d-none");
	              removeConfirmError.textContent = "";
	            }
	          }

	          function openRemoveModal(id, tier, encodedClientName){
	            const clientName = decodeURIComponent(encodedClientName || "");
	            removeDeliveryId.value = id;
	            removeDeliveryTier.value = tier;
	            removeExpectedClientName.value = clientName;
	            removeDeliveryClientName.textContent = clientName;
	            removeConfirmName.value = "";
	            removeConfirmBtn.disabled = true;
	            removeConfirmError.classList.add("d-none");
	            removeConfirmError.textContent = "";
	            removeModal.show();
	          }

	          for (const btn of sortButtons){
	            btn.addEventListener("click", () => {
	              const key = btn.dataset.sortKey;
	              if (!key) return;
	              if (activeSortKey === key){
	                activeSortDir = activeSortDir === "asc" ? "desc" : "asc";
	              } else {
	                activeSortKey = key;
	                activeSortDir = "asc";
	              }
	              updateSortIndicators();
	              renderRows(sortItems(rawItems));
	            });
	          }

	          removeConfirmName.addEventListener("input", validateRemoveModalInput);
	          removeForm.addEventListener("submit", (event) => {
	            if (removeConfirmName.value !== removeExpectedClientName.value){
	              event.preventDefault();
	              validateRemoveModalInput();
	            }
	          });

	          window.sendNow = sendNow;
	          window.deleteDelivery = deleteDelivery;
	          window.openRemoveModal = openRemoveModal;

	          refreshBtn.addEventListener("click", loadDeliveries);
	          applyFilters.addEventListener("click", loadDeliveries);

	          restoreFilterPrefs();
	          renderFlashFromQuery();
	          updateSortIndicators();
	          loadDeliveries();
	        </script>
      </body>
    </html>
    """
    return HTMLResponse(html)


@router.get("/queue", response_class=HTMLResponse)
async def queue_page():
    html = """
    <html>
      <head>
        <title>Queue</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet" />
        <style>
          /* Icon-only action buttons in the queue table. */
          .icon-action-btn {
            border: 0 !important;
            background: transparent !important;
            color: var(--bs-secondary) !important;
          }
          .icon-action-btn:hover:not(:disabled) {
            background: rgba(var(--bs-secondary-rgb), 0.12) !important;
          }
          .icon-action-btn:active:not(:disabled) {
            background: rgba(var(--bs-secondary-rgb), 0.2) !important;
          }
          .icon-action-btn:focus-visible {
            box-shadow: 0 0 0 .2rem rgba(var(--bs-primary-rgb), .25);
          }
          .icon-action-btn:disabled {
            opacity: 0.35;
          }
        </style>
      </head>
      <body class="bg-light">
        <div class="container py-4">
          <div class="d-flex flex-wrap align-items-center justify-content-between mb-3">
            <div>
              <h2 class="mb-0">Job Queue</h2>
              <div class="text-muted small">/ui/queue</div>
            </div>
            <div class="d-flex align-items-center gap-2">
              <a href="/ui/deliveries" class="btn btn-outline-primary btn-sm">Deliveries</a>
              <button id="filterBtn" class="btn btn-outline-primary btn-sm" data-bs-toggle="modal" data-bs-target="#filterModal">Filters</button>
              <button id="refreshBtn" class="btn btn-outline-secondary btn-sm">Refresh</button>
              <div class="text-muted small" id="lastUpdated"></div>
            </div>
          </div>

          <div class="text-muted small mb-2">
            Auto-refreshes every 30 seconds. Filters apply on each refresh.
          </div>

          <div class="table-responsive">
            <table class="table table-sm align-middle table-hover bg-white shadow-sm">
              <thead class="table-light">
                <tr>
                  <th scope="col">Job ID</th>
                  <th scope="col">Status</th>
                  <th scope="col">Que #</th>
                  <th scope="col">Stage</th>
                  <th scope="col">Done/Total</th>
                  <th scope="col">Failed</th>
                  <th scope="col">Current</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody id="queueBody">
                <tr><td colspan="8" class="text-muted">Loading…</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Filters Modal -->
        <div class="modal fade" id="filterModal" tabindex="-1" aria-labelledby="filterModalLabel" aria-hidden="true">
          <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
              <div class="modal-header">
                <h5 class="modal-title" id="filterModalLabel">Filters</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
              </div>
              <div class="modal-body">
                <div class="mb-3">
                  <div class="fw-semibold mb-1">Statuses</div>
                  <div class="d-flex flex-wrap gap-2">
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="queued">
                      <span class="form-check-label">Queued</span>
                    </label>
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="running">
                      <span class="form-check-label">Running</span>
                    </label>
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="paused">
                      <span class="form-check-label">Paused</span>
                    </label>
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="completed">
                      <span class="form-check-label">Completed</span>
                    </label>
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="failed">
                      <span class="form-check-label">Failed</span>
                    </label>
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="canceled">
                      <span class="form-check-label">Canceled</span>
                    </label>
                  </div>
                </div>
                <div>
                  <div class="fw-semibold mb-1">Time window</div>
                  <div class="d-flex flex-wrap align-items-center gap-2">
                    <label class="d-flex align-items-center gap-2 mb-0">
                      <span>From</span>
                      <select id="fromHours" class="form-select form-select-sm">
                        <option value="0">Now</option>
                        <option value="1">1h ago</option>
                        <option value="2">2h ago</option>
                        <option value="4">4h ago</option>
                        <option value="6">6h ago</option>
                        <option value="12">12h ago</option>
                        <option value="24">24h ago</option>
                      </select>
                    </label>
                    <span>to</span>
                    <label class="d-flex align-items-center gap-2 mb-0">
                      <select id="toHours" class="form-select form-select-sm">
                        <option value="">Any time</option>
                        <option value="1">1h ago</option>
                        <option value="2">2h ago</option>
                        <option value="4">4h ago</option>
                        <option value="6">6h ago</option>
                        <option value="12">12h ago</option>
                        <option value="24">24h ago</option>
                      </select>
                    </label>
                  </div>
                </div>
              </div>
              <div class="modal-footer">
                <button type="button" class="btn btn-outline-secondary btn-sm" id="clearFilters">Clear all</button>
                <button type="button" class="btn btn-primary btn-sm" id="applyFilters" data-bs-dismiss="modal">Apply</button>
              </div>
            </div>
          </div>
        </div>

        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
        <script>
          // Badges
          const badgeHTML = (text) => {
            const t = (text || "unknown").toLowerCase();
            let bg="#f3f4f6", fg="#374151", bd="#d1d5db";
            if (t==="completed"){bg="#dcfce7";fg="#166534";bd="#86efac";}
            else if (t==="running"){bg="#dbeafe";fg="#1e40af";bd="#93c5fd";}
            else if (t==="failed"){bg="#fee2e2";fg="#991b1b";bd="#fca5a5";}
            else if (t==="queued"){bg="#fef9c3";fg="#854d0e";bd="#fde047";}
            else if (t==="paused"){bg="#fff7ed";fg="#9a3412";bd="#fdba74";}
            return `<span style="display:inline-block;padding:2px 10px;border-radius:999px;border:1px solid ${bd};background:${bg};color:${fg};font-size:12px;line-height:18px">${text}</span>`;
          };

          const safe = (v) => (v === null || v === undefined) ? "" : String(v);

          const FILTER_STORAGE_KEY = "queueFiltersV1";
          const DEFAULT_FILTERS = {
            statuses: ["queued", "running", "paused", "completed", "failed", "canceled"],
            fromH: "0",
            toH: "",
          };

          function loadFilters() {
            try {
              const raw = localStorage.getItem(FILTER_STORAGE_KEY);
              if (!raw) return { ...DEFAULT_FILTERS };
              const parsed = JSON.parse(raw);
              return {
                statuses: Array.isArray(parsed.statuses) ? parsed.statuses : DEFAULT_FILTERS.statuses,
                fromH: parsed.fromH ?? DEFAULT_FILTERS.fromH,
                toH: parsed.toH ?? DEFAULT_FILTERS.toH,
              };
            } catch (_) {
              return { ...DEFAULT_FILTERS };
            }
          }

          function saveFilters(filters) {
            try {
              localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(filters));
            } catch (_) {}
          }

          function setFiltersUI(filters) {
            const statusSet = new Set(filters.statuses || []);
            document.querySelectorAll(".statusFilter").forEach((el) => {
              el.checked = statusSet.has(el.value);
            });
            document.getElementById("fromHours").value = filters.fromH ?? DEFAULT_FILTERS.fromH;
            document.getElementById("toHours").value = filters.toH ?? DEFAULT_FILTERS.toH;
          }

          function getSelectedStatuses() {
            return Array.from(document.querySelectorAll(".statusFilter:checked")).map(el => el.value);
          }

          function currentFilters() {
            const statuses = getSelectedStatuses();
            const fromH = document.getElementById("fromHours").value;
            const toH = document.getElementById("toHours").value;
            return { statuses, fromH, toH };
          }

          async function apiJSON(url, opts) {
            const res = await fetch(url, opts || {});
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
          }

          let allItems = [];

          function renderFilteredItems() {
            const body = document.getElementById("queueBody");
            const { statuses, fromH, toH } = currentFilters();
            const statusSet = new Set(statuses.map(s => s.toLowerCase()));
            const nowSec = Date.now() / 1000;
            const fromVal = fromH === "" ? null : parseFloat(fromH);
            const toVal = toH === "" ? null : parseFloat(toH);

            const filtered = allItems.filter(item => {
              const st = (item.display_status || item.status || "").toLowerCase();
              if (statusSet.size && !statusSet.has(st)) return false;
              const createdAt = item.created_at || 0;
              const hoursAgo = (nowSec - createdAt) / 3600;
              if (fromVal !== null && hoursAgo < fromVal) return false;
              if (toVal !== null && hoursAgo > toVal) return false;
              return true;
            });

            if (!filtered.length) {
              body.innerHTML = `<tr><td colspan="8" style="color:#6b7280;">No jobs found.</td></tr>`;
              return;
            }

            body.innerHTML = filtered.map(item => {
              const jid = item.job_id;
              const status = item.display_status || item.status || "unknown";
              const queuePos = item.queue_position || "";
              const stage = safe(item.stage);
              const total = safe(item.pages_total);
              const done = safe(item.pages_done);
              const failed = safe(item.pages_failed);
              const current = safe(item.current);
              return `
                <tr>
                  <td style="font-family:ui-monospace, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size:12px;">
                    <a href="/ui/job/${jid}">${jid}</a>
                  </td>
                  <td>${badgeHTML(status)}</td>
                  <td>${queuePos}</td>
                  <td>${stage}</td>
                  <td>${done}/${total}</td>
                  <td>${failed}</td>
                  <td style="max-width:420px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${current}</td>
                  <td>${actionsHTML(item)}</td>
                </tr>
              `;
            }).join("");
          }

          async function cancelJob(jobId) {
            await apiJSON(`/ui/job/${jobId}/cancel`, { method: "POST" });
            await refreshQueue();
          }

          async function pauseJob(jobId) {
            await apiJSON(`/ui/job/${jobId}/pause`, { method: "POST" });
            await refreshQueue();
          }

          async function resumeJob(jobId) {
            await apiJSON(`/ui/job/${jobId}/resume`, { method: "POST" });
            await refreshQueue();
          }

          async function moveJob(jobId, dir) {
            await apiJSON(`/ui/job/${jobId}/move?dir=${encodeURIComponent(dir)}`, { method: "POST" });
            await refreshQueue();
          }

          // --- Icons (Bootstrap Icons) ---
          const ICONS = {
            cancel: `<i class="bi bi-x-lg"></i>`,
            pause: `<i class="bi bi-pause-fill"></i>`,
            resume: `<i class="bi bi-play-fill"></i>`,
            up: `<i class="bi bi-chevron-up"></i>`,
            down: `<i class="bi bi-chevron-down"></i>`,
            top: `<i class="bi bi-arrow-up-square"></i>`,
            bottom: `<i class="bi bi-arrow-down-square"></i>`,
          };

          const iconBtnStyle = (enabled) => `
            width:40px;height:40px;
            display:inline-flex;align-items:center;justify-content:center;
            padding:0;
          `;

          const iconWrapStyle = `
            width:22px;height:22px;display:block;font-size:20px;font-weight:700;line-height:1;
          `;

          function iconButton({ title, enabled, onClick, iconKey }) {
            const disabledAttr = enabled ? "" : "disabled";
            const handlerAttr = enabled ? `onclick="${onClick}"` : "";
            return `
              <button title="${title}" aria-label="${title}"
                class="btn btn-sm icon-action-btn ${enabled ? '' : 'disabled'}"
                style="${iconBtnStyle(enabled)}" ${disabledAttr} ${handlerAttr}
                data-bs-toggle="tooltip" data-bs-placement="top" data-bs-title="${title}">
                <span style="${iconWrapStyle}">${ICONS[iconKey]}</span>
              </button>
            `;
          }

          function actionsHTML(item) {
            const jid = item.job_id;

            const cancelBtn = iconButton({
              title: "Cancel",
              enabled: !!item.can_cancel,
              iconKey: "cancel",
              onClick: `cancelJob('${jid}')`,
            });

            const pauseResumeBtn = item.can_pause
              ? iconButton({
                  title: "Pause",
                  enabled: true,
                  iconKey: "pause",
                  onClick: `pauseJob('${jid}')`,
                })
              : (item.can_resume
                  ? iconButton({
                      title: "Resume",
                      enabled: true,
                      iconKey: "resume",
                      onClick: `resumeJob('${jid}')`,
                    })
                  : iconButton({
                      title: "Pause",
                      enabled: false,
                      iconKey: "pause",
                      onClick: "",
                    })
                );

            const moveTop = iconButton({
              title: "Move to top",
              enabled: !!item.can_move,
              iconKey: "top",
              onClick: `moveJob('${jid}','top')`,
            });

            const moveUp = iconButton({
              title: "Move up",
              enabled: !!item.can_move,
              iconKey: "up",
              onClick: `moveJob('${jid}','up')`,
            });

            const moveDown = iconButton({
              title: "Move down",
              enabled: !!item.can_move,
              iconKey: "down",
              onClick: `moveJob('${jid}','down')`,
            });

            const moveBottom = iconButton({
              title: "Move to bottom",
              enabled: !!item.can_move,
              iconKey: "bottom",
              onClick: `moveJob('${jid}','bottom')`,
            });

            return `
              <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
                ${cancelBtn}
                ${pauseResumeBtn}
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                  ${moveTop}
                  ${moveUp}
                  ${moveDown}
                  ${moveBottom}
                </div>
              </div>
            `;
          }

          async function refreshQueue() {
            const body = document.getElementById("queueBody");
            try {
              allItems = await apiJSON(`/ui/api/queue`);
              renderFilteredItems();
              const now = new Date();
              document.getElementById("lastUpdated").textContent = `Last updated: ${now.toLocaleTimeString()}`;
            } catch (e) {
              body.innerHTML = `<tr><td colspan="8" style="color:#ef4444;">Failed to load queue data.</td></tr>`;
            }
          }

          document.getElementById("refreshBtn").addEventListener("click", () => refreshQueue());
          document.getElementById("applyFilters").addEventListener("click", () => {
            const filters = currentFilters();
            saveFilters(filters);
            renderFilteredItems();
            refreshQueue();
          });
          document.getElementById("clearFilters").addEventListener("click", () => {
            setFiltersUI(DEFAULT_FILTERS);
            saveFilters(DEFAULT_FILTERS);
            renderFilteredItems();
          });
          document.getElementById("filterModal").addEventListener("show.bs.modal", () => {
            const filters = loadFilters();
            setFiltersUI(filters);
          });
          const tooltips = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]')).map(el => new bootstrap.Tooltip(el));
          window.cancelJob = cancelJob;
          window.pauseJob = pauseJob;
          window.resumeJob = resumeJob;
          window.moveJob = moveJob;

          setFiltersUI(loadFilters());
          refreshQueue();
          setInterval(refreshQueue, 30000);
        </script>
      </body>
    </html>
    """
    return html


@router.get("/job/{job_id}", response_class=HTMLResponse)
async def job_page(job_id: str):
    status = await get_status(job_id) or "unknown"
    prog = await get_progress(job_id)
    logs = await get_log(job_id, 300)

    stage = prog.get("stage", "")
    total = prog.get("pages_total", "")
    done = prog.get("pages_done", "")
    failed = prog.get("pages_failed", "")
    skipped = prog.get("pages_skipped", "")
    current = prog.get("current", "")
    log_text = "\n".join(logs) if logs else ""

    def _level_of(line: str) -> str:
        if isinstance(line, str) and len(line) >= 3 and line.startswith("[") and line[2] == "]":
            return line[1].upper()
        return "I"

    simple_logs: list[str] = []
    debug_logs: list[str] = []
    for line in logs or []:
        lvl = _level_of(line)
        if lvl == "D":
            debug_logs.append(line)
        else:
            simple_logs.append(line)

    simple_log_text = "\n".join(simple_logs)
    full_log_text = log_text
    thread_run_lines = []
    seen_pairs = set()
    for line in logs or []:
        if ("thread_id" in line) or ("run_id" in line):
            if line not in seen_pairs:
                thread_run_lines.append(line)
                seen_pairs.add(line)

    full_logs_json = json.dumps(full_log_text)
    simple_logs_json = json.dumps(simple_log_text)

    if thread_run_lines:
        thread_items = "".join([f"<li class='list-group-item py-1 px-2'>{line}</li>" for line in thread_run_lines])
        thread_run_section = f"""
          <div class="card mt-3">
            <div class="card-header d-flex align-items-center justify-content-between">
              <span>Thread/Run IDs found in logs</span>
              <button class="btn btn-sm btn-outline-secondary" type="button" data-bs-toggle="collapse" data-bs-target="#threadRunCollapse" aria-expanded="false" aria-controls="threadRunCollapse">
                Show/Hide
              </button>
            </div>
            <div class="collapse" id="threadRunCollapse">
              <div class="card-body p-0">
                <ul class="list-group list-group-flush small">
                  {thread_items}
                </ul>
              </div>
            </div>
          </div>
        """
    else:
        thread_run_section = """
          <div class="alert alert-secondary mt-3 mb-0 py-2 small">
            No thread/run IDs found in the last 300 log lines.
          </div>
        """

    html = f"""
    <html>
      <head>
        <title>Job {job_id}</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet" />
      </head>
      <body class="bg-light">
        <div class="container py-4">
          <div class="d-flex align-items-center justify-content-between mb-3">
            <div>
              <div class="small text-muted"><a href="/ui/queue">← Back to queue</a></div>
              <h2 class="mb-1">Job</h2>
              <div class="text-monospace small text-dark">{job_id}</div>
            </div>
            <div>{_badge(status)}</div>
          </div>

          <div class="card mb-3">
            <div class="card-body d-flex flex-wrap gap-3 small">
              <div><strong>Stage:</strong> {stage}</div>
              <div><strong>Done/Total:</strong> {done}/{total}</div>
              <div><strong>Failed:</strong> {failed}</div>
              <div><strong>Skipped:</strong> {skipped}</div>
              <div class="text-truncate" style="max-width: 600px;"><strong>Current:</strong> {current}</div>
            </div>
          </div>

          <div class="card mb-3">
            <div class="card-header d-flex align-items-center justify-content-between">
              <h5 class="mb-0">Progress JSON</h5>
              <a href="/result/{job_id}" class="small">View result JSON</a>
            </div>
            <div class="card-body">
              <pre class="bg-dark text-light p-3 rounded small mb-0" style="overflow:auto;">{prog}</pre>
            </div>
          </div>

          <div class="card">
            <div class="card-header">
              <div class="d-flex align-items-center justify-content-between">
                <h5 class="mb-0">Logs (last 300)</h5>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="debugToggle">
                  <label class="form-check-label" for="debugToggle">Debugging</label>
                </div>
              </div>
            </div>
            <div class="card-body">
              {thread_run_section}
              <pre id="logText" class="bg-dark text-success p-3 rounded small mt-3 mb-0" style="overflow:auto; white-space:pre-wrap;">{simple_log_text}</pre>
            </div>
          </div>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
        <script>
          const fullLogs = {full_logs_json};
          const simpleLogs = {simple_logs_json};
          const logPre = document.getElementById("logText");
          const debugToggle = document.getElementById("debugToggle");

          function updateLogs() {{
            const useDebug = debugToggle.checked;
            logPre.textContent = useDebug ? fullLogs : simpleLogs;
          }}
          debugToggle.addEventListener("change", updateLogs);
          updateLogs();
        </script>
      </body>
    </html>
    """
    return html
