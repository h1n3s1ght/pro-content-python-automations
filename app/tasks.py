import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import httpx
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from .celery_app import celery_app
from .errors import OperationCanceled, PauseRequested
from .logging_utils import log_info, log_warn, log_error
from .outbox import (
    claim_delivery,
    claim_site_check,
    mark_delivery_failed,
    mark_delivery_sent,
    mark_site_check_failed,
    mark_site_ready,
)
from .db import get_sessionmaker
from .db_models import DeliveryOutbox, RecentlyDeletedJobCopy
from .workflow import run_workflow
from .resume_workflow import run_resume_workflow
from .storage import (
    set_status,
    set_result,
    set_progress,
    is_paused,
    is_canceled,
    get_progress,
    get_payload,
    is_resume_mode,
)
from .monthly_logs import upload_monthly_queue_logs
from .payload_store import (
    load_payload_json,
    maybe_archive_payload_to_s3,
    purge_payload_file,
)

logger = logging.getLogger(__name__)

DELIVERY_MODE = os.getenv("DELIVERY_MODE", "manual").strip().lower()
ZAPIER_WEBHOOK_URL = os.getenv("ZAPIER_WEBHOOK_URL", "").strip()
DELIVERY_HTTP_TIMEOUT = float(os.getenv("DELIVERY_HTTP_TIMEOUT", "30"))
DUE_DELIVERY_STATUSES = ("COMPLETED_PENDING_SEND", "READY_TO_SEND", "FAILED")
SITE_CHECK_TIMEOUT = float(os.getenv("SITE_CHECK_TIMEOUT", "10"))
SITE_CHECK_INITIAL_INTERVAL_SECONDS = int(os.getenv("SITE_CHECK_INITIAL_INTERVAL_SECONDS", "300"))
SITE_CHECK_INITIAL_ATTEMPTS = int(os.getenv("SITE_CHECK_INITIAL_ATTEMPTS", "12"))
SITE_CHECK_LONG_INTERVAL_SECONDS = int(os.getenv("SITE_CHECK_LONG_INTERVAL_SECONDS", "3600"))


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    soft_time_limit=60 * 29,
    time_limit=60 * 30,
)
def run_full_job(self, job_id: str, payload: dict):
    try:
        if asyncio.run(is_resume_mode(job_id)):
            asyncio.run(log_info(job_id, "resume_mode_skip_full_workflow"))
            return
        if asyncio.run(is_paused(job_id)):
            asyncio.run(_paused_wait(job_id))
            raise self.retry(countdown=30)
        asyncio.run(_run(job_id, payload))
    except SoftTimeLimitExceeded:
        asyncio.run(_timeout(job_id))
        raise self.retry(countdown=60)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    soft_time_limit=60 * 29,
    time_limit=60 * 30,
)
def run_resume_job(self, job_id: str):
    try:
        if asyncio.run(is_paused(job_id)):
            asyncio.run(_paused_wait(job_id))
            raise self.retry(countdown=30)
        payload = asyncio.run(get_payload(job_id))
        if payload is None:
            asyncio.run(set_status(job_id, "failed"))
            asyncio.run(set_result(job_id, {"error": "resume_missing_payload"}))
            asyncio.run(set_progress(job_id, {"stage": "failed"}))
            asyncio.run(log_error(job_id, "resume_failed_missing_payload"))
            return
        asyncio.run(_run_resume(job_id, payload))
    except SoftTimeLimitExceeded:
        asyncio.run(_timeout(job_id))
        raise self.retry(countdown=60)


async def _paused_wait(job_id: str):
    await set_status(job_id, "paused")
    await log_info(job_id, "job_paused_waiting")


async def _timeout(job_id: str):
    await set_status(job_id, "queued")
    await set_progress(job_id, {"stage": "queued", "reason": "timeout_requeued"})
    await log_warn(job_id, "job_timeout_requeued")


async def _check_stop(job_id: str):
    if await is_canceled(job_id):
        raise OperationCanceled("job canceled")
    if await is_paused(job_id):
        raise PauseRequested("job paused")


async def _run(job_id: str, payload: dict):
    await _execute(job_id, payload, run_workflow)


async def _run_resume(job_id: str, payload: dict):
    await _execute(job_id, payload, run_resume_workflow)


async def _execute(job_id: str, payload: dict, workflow_fn):
    await set_status(job_id, "running")
    await set_progress(job_id, {"stage": "starting"})
    try:
        await _check_stop(job_id)
        await log_info(job_id, "job_started")
        result = await workflow_fn(payload, job_id=job_id)
        await _check_stop(job_id)
        await set_result(job_id, result)
        await set_status(job_id, "completed")
        cur = await get_progress(job_id) or {}
        cur["stage"] = "completed"
        await set_progress(job_id, cur)
        await log_info(job_id, "job_completed")
    except PauseRequested:
        await set_status(job_id, "paused")
        await set_progress(job_id, {"stage": "paused"})
        await log_info(job_id, "job_paused_by_user")
    except OperationCanceled:
        await set_status(job_id, "canceled")
        await set_progress(job_id, {"stage": "canceled"})
        await log_info(job_id, "job_canceled_by_user")
    except Exception as e:
        await set_status(job_id, "failed")
        await set_result(job_id, {"error": str(e)})
        await set_progress(job_id, {"stage": "failed"})
        await log_error(job_id, f"job_failed: {str(e)}")
        raise


@celery_app.task(
    name="app.tasks.upload_previous_month_queue_logs",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def upload_previous_month_queue_logs():
    tz = ZoneInfo("America/Chicago")
    now = datetime.now(tz=tz)

    year = now.year
    month = now.month - 1
    if month == 0:
        month = 12
        year -= 1

    month_yyyy_mm = f"{year:04d}-{month:02d}"
    asyncio.run(upload_monthly_queue_logs(month_yyyy_mm))


@celery_app.task(
    name="app.tasks.enqueue_due_deliveries",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def enqueue_due_deliveries():
    now = datetime.now(timezone.utc)
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        stmt = (
            select(DeliveryOutbox.id)
            .where(
                DeliveryOutbox.scheduled_for.is_not(None),
                DeliveryOutbox.scheduled_for <= now,
                DeliveryOutbox.status.in_(DUE_DELIVERY_STATUSES),
            )
            .order_by(DeliveryOutbox.scheduled_for.asc())
        )
        delivery_ids = [str(row[0]) for row in session.execute(stmt).all()]
    finally:
        session.close()

    for delivery_id in delivery_ids:
        send_delivery.delay(delivery_id)

    logger.info("enqueue_due_deliveries count=%s", len(delivery_ids))
    return len(delivery_ids)


def _next_site_check_time(attempts: int) -> datetime:
    interval = SITE_CHECK_INITIAL_INTERVAL_SECONDS
    if attempts > SITE_CHECK_INITIAL_ATTEMPTS:
        interval = SITE_CHECK_LONG_INTERVAL_SECONDS
    return datetime.now(timezone.utc) + timedelta(seconds=interval)


def _resolve_target_url(row: dict) -> str:
    override_url = str(row.get("override_target_url") or "").strip()
    default_url = str(row.get("default_target_url") or "").strip()
    return override_url or default_url


def _build_delivery_payload(row: dict, target_url: str) -> dict:
    return {
        "delivery_id": str(row.get("id") or ""),
        "job_id": row.get("job_id"),
        "client_name": row.get("client_name"),
        "payload_s3_key": row.get("payload_s3_key"),
        "target_url": target_url,
        "preview_url": row.get("preview_url"),
    }


def _extract_data_content(content: dict) -> dict:
    if not isinstance(content, dict):
        return {}
    data = content.get("data")
    if isinstance(data, dict) and isinstance(data.get("content"), dict):
        return data["content"]
    return content


def _build_zapier_payload(row: dict, target_url: str, content: dict) -> dict:
    data_content = _extract_data_content(content)
    return {
        "metadata": {"deliveryDomain": target_url},
        "data": {"content": data_content},
    }


@celery_app.task(
    bind=True,
    autoretry_for=(),
)
def send_delivery(self, delivery_id: str):
    try:
        row = claim_delivery(delivery_id)
    except Exception as exc:
        logger.exception("send_delivery_claim_failed delivery_id=%s err=%s", delivery_id, exc)
        raise

    if not row:
        logger.info("send_delivery_noop delivery_id=%s", delivery_id)
        return

    mode = DELIVERY_MODE

    content = None
    if mode in ("manual", "zapier", "automatic"):
        # manual:
        #   Always require a user-provided override_target_url via /ui/deliveries.
        # zapier:
        #   May prefill override_target_url (from an external system later); if missing, user can still enter it.
        # automatic:
        #   RESERVED for future; will eventually pull per-client delivery URL from a DB and auto-send.
        #
        # For now, manual/zapier/automatic all deliver via the Zapier webhook.
        if mode == "manual":
            resolved = str(row.get("override_target_url") or "").strip()
        else:
            # Prefer override_target_url if present, otherwise fall back to default_target_url.
            resolved = _resolve_target_url(row)

        if not resolved:
            mark_delivery_failed(delivery_id, "missing delivery_url")
            logger.warning("send_delivery_missing_delivery_url delivery_id=%s mode=%s", delivery_id, mode)
            return

        target_url = resolved
        payload = _build_delivery_payload(row, target_url)

        if not ZAPIER_WEBHOOK_URL:
            mark_delivery_failed(delivery_id, "missing ZAPIER_WEBHOOK_URL")
            logger.warning("send_delivery_missing_zapier_url delivery_id=%s", delivery_id)
            return

        content = load_payload_json(row.get("payload_s3_key") or "")
        if content is None:
            mark_delivery_failed(delivery_id, "missing payload content")
            logger.warning("send_delivery_missing_payload delivery_id=%s", delivery_id)
            return

        payload = _build_zapier_payload(row, target_url, content)
        url = ZAPIER_WEBHOOK_URL
    elif mode == "direct":
        target_url = _resolve_target_url(row)
        if not target_url:
            mark_delivery_failed(delivery_id, "missing target_url")
            logger.warning("send_delivery_missing_target delivery_id=%s", delivery_id)
            return
        payload = _build_delivery_payload(row, target_url)
        url = target_url
    else:
        mark_delivery_failed(delivery_id, f"invalid DELIVERY_MODE: {mode}")
        logger.warning("send_delivery_invalid_mode delivery_id=%s mode=%s", delivery_id, mode)
        return

    try:
        with httpx.Client(timeout=DELIVERY_HTTP_TIMEOUT) as client:
            resp = client.post(url, json=payload)
    except Exception as exc:
        mark_delivery_failed(delivery_id, f"request_error: {exc}")
        logger.exception("send_delivery_request_error delivery_id=%s", delivery_id)
        return

    if 200 <= resp.status_code < 300:
        mark_delivery_sent(delivery_id)
        logger.info("send_delivery_sent delivery_id=%s status=%s", delivery_id, resp.status_code)
        try:
            job_id = str(row.get("job_id") or "").strip()
            client_name = str(row.get("client_name") or "").strip()
            if job_id:
                if isinstance(content, dict):
                    maybe_archive_payload_to_s3(job_id, client_name or job_id, content)
        except Exception as exc:
            logger.warning("send_delivery_post_success_hooks_failed delivery_id=%s err=%s", delivery_id, exc)
        return

    resp_body = (resp.text or "")[:800]
    mark_delivery_failed(delivery_id, f"{resp.status_code}: {resp_body}")
    logger.warning("send_delivery_failed delivery_id=%s status=%s", delivery_id, resp.status_code)


@celery_app.task(
    name="app.tasks.enqueue_due_site_checks",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def enqueue_due_site_checks():
    now = datetime.now(timezone.utc)
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        stmt = (
            select(DeliveryOutbox.id)
            .where(
                DeliveryOutbox.status == "WAITING_FOR_SITE",
                DeliveryOutbox.site_check_next_at.is_not(None),
                DeliveryOutbox.site_check_next_at <= now,
            )
            .order_by(DeliveryOutbox.site_check_next_at.asc())
        )
        delivery_ids = [str(row[0]) for row in session.execute(stmt).all()]
    finally:
        session.close()

    for delivery_id in delivery_ids:
        check_site_ready.delay(delivery_id)

    logger.info("enqueue_due_site_checks count=%s", len(delivery_ids))
    return len(delivery_ids)


@celery_app.task(
    bind=True,
    autoretry_for=(),
)
def check_site_ready(self, delivery_id: str):
    try:
        row = claim_site_check(delivery_id)
    except Exception as exc:
        logger.exception("check_site_ready_claim_failed delivery_id=%s err=%s", delivery_id, exc)
        raise

    if not row:
        logger.info("check_site_ready_noop delivery_id=%s", delivery_id)
        return

    preview_url = str(row.get("preview_url") or "").strip()
    attempts = int(row.get("site_check_attempts") or 0) + 1

    if not preview_url:
        next_check_at = _next_site_check_time(attempts)
        mark_site_check_failed(
            delivery_id,
            next_check_at=next_check_at,
            attempts=attempts,
            error_message="missing preview_url",
        )
        logger.warning("check_site_ready_missing_preview delivery_id=%s", delivery_id)
        return

    try:
        with httpx.Client(timeout=SITE_CHECK_TIMEOUT) as client:
            resp = client.get(preview_url)
    except Exception as exc:
        next_check_at = _next_site_check_time(attempts)
        mark_site_check_failed(
            delivery_id,
            next_check_at=next_check_at,
            attempts=attempts,
            error_message=f"request_error: {exc}",
        )
        logger.exception("check_site_ready_request_error delivery_id=%s", delivery_id)
        return

    if 200 <= resp.status_code < 300:
        mark_site_ready(delivery_id)
        logger.info("check_site_ready_ok delivery_id=%s status=%s", delivery_id, resp.status_code)
        send_delivery.delay(str(delivery_id))
        return

    next_check_at = _next_site_check_time(attempts)
    mark_site_check_failed(
        delivery_id,
        next_check_at=next_check_at,
        attempts=attempts,
        error_message=f"{resp.status_code}: {(resp.text or '')[:400]}",
    )
    logger.warning(
        "check_site_ready_failed delivery_id=%s status=%s next_check_at=%s",
        delivery_id,
        resp.status_code,
        next_check_at.isoformat(),
    )


@celery_app.task(
    name="app.tasks.purge_local_payload",
    bind=True,
    autoretry_for=(),
)
def purge_local_payload(self, job_id: str):
    return purge_payload_file(job_id)


@celery_app.task(
    name="app.tasks.finalize_deleted_job_copy",
    bind=True,
    autoretry_for=(),
)
def finalize_deleted_job_copy(self, job_id: str):
    """
    Final cleanup for a soft-deleted job copy:
    - remove the recently-deleted DB row (when due)
    - purge the on-disk payload file (best-effort)
    """
    job_id = str(job_id or "").strip()
    if not job_id:
        return False

    now = datetime.now(timezone.utc)
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        row = session.execute(
            select(RecentlyDeletedJobCopy).where(RecentlyDeletedJobCopy.job_id == job_id)
        ).scalar_one_or_none()
        if row is None:
            purge_payload_file(job_id)
            return True

        destroy_after = row.destroy_after
        if destroy_after and destroy_after > now:
            delay = int((destroy_after - now).total_seconds())
            # Avoid ultra-short reschedules that can loop.
            delay = max(delay, 60)
            finalize_deleted_job_copy.apply_async(args=[job_id], countdown=delay)
            return False

        session.delete(row)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("finalize_deleted_job_copy_failed job_id=%s err=%s", job_id, exc)
        raise
    finally:
        session.close()

    purge_payload_file(job_id)
    return True
