from __future__ import annotations

import os
import re
import uuid
import logging
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert

from .db import get_sessionmaker
from .db_models import DeliveryOutbox

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CONDENSE_RE = re.compile(r"[^a-z0-9]+")
READY_STATUSES = ("READY", "READY_TO_SEND", "FAILED", "COMPLETED_PENDING_SEND")
logger = logging.getLogger(__name__)


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def slugify(value: Any) -> str:
    text = _clean_str(value).lower()
    text = _SLUG_RE.sub("-", text)
    return text.strip("-")


def condense_name(value: Any) -> str:
    text = _clean_str(value).lower()
    text = _CONDENSE_RE.sub("", text)
    return text.strip()


def _job_details_base_url(job_details: Dict[str, Any]) -> str:
    if not isinstance(job_details, dict):
        return ""
    for key in ("base_url", "baseUrl", "baseURL", "target_base_url", "targetBaseUrl"):
        val = _clean_str(job_details.get(key))
        if val:
            return val
    return ""


def _resolve_base_url(client_name: str, job_details: Dict[str, Any]) -> str:
    base_url = _job_details_base_url(job_details)
    if base_url:
        return base_url.rstrip("/")
    template = _clean_str(os.getenv("DELIVERY_BASE_URL_TEMPLATE", ""))
    slug = slugify(client_name)
    if not slug:
        raise ValueError("client_name missing for delivery base URL")
    if not template:
        raise ValueError("DELIVERY_BASE_URL_TEMPLATE is required when job_details base_url is missing")
    return template.format(slug=slug).rstrip("/")


def _resolve_target_path() -> str:
    template = _clean_str(
        os.getenv("DELIVERY_TARGET_PATH_TEMPLATE", "/wp-json/{namespace}/v1/content")
    )
    namespace = _clean_str(os.getenv("DELIVERY_TARGET_NAMESPACE", ""))
    if "{namespace}" in template and not namespace:
        raise ValueError("DELIVERY_TARGET_NAMESPACE is required for delivery target path")
    path = template.format(namespace=namespace)
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def build_default_target_url(client_name: str, job_details: Dict[str, Any]) -> str:
    base_url = _resolve_base_url(client_name, job_details)
    return f"{base_url}{_resolve_target_path()}"


def build_preview_url(condensed_name: str) -> str:
    base_domain = _clean_str(os.getenv("PREVIEW_BASE_DOMAIN", "wp-premium-hosting.com"))
    namespace = _clean_str(os.getenv("PREVIEW_NAMESPACE", "kaseya"))
    if not condensed_name:
        raise ValueError("condensed business name missing for preview URL")
    if not base_domain:
        raise ValueError("PREVIEW_BASE_DOMAIN is required for preview URL")
    if not namespace:
        raise ValueError("PREVIEW_NAMESPACE is required for preview URL")
    return f"https://{condensed_name}.{base_domain}/wp-json/{namespace}/site/status"


def _safe_db_location() -> str:
    raw = _clean_str(os.getenv("DATABASE_URL", ""))
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


def enqueue_delivery_outbox(
    *,
    job_id: str,
    client_name: str,
    payload_s3_key: str,
    default_target_url: str,
    preview_url: str | None = None,
    site_check_next_at: datetime | None = None,
    site_check_attempts: int = 0,
    status: str = "COMPLETED_PENDING_SEND",
) -> uuid.UUID | None:
    logger.info(
        "outbox_enqueue_start job_id=%s client=%s s3_key=%s status=%s db=%s",
        job_id,
        client_name,
        payload_s3_key,
        status,
        _safe_db_location(),
    )
    stmt = (
        insert(DeliveryOutbox)
        .values(
            job_id=job_id,
            client_name=client_name,
            payload_s3_key=payload_s3_key,
            default_target_url=default_target_url,
            preview_url=preview_url,
            site_check_next_at=site_check_next_at,
            site_check_attempts=site_check_attempts,
            status=status,
        )
        .on_conflict_do_update(
            index_elements=[DeliveryOutbox.job_id],
            set_={
                "client_name": client_name,
                "payload_s3_key": payload_s3_key,
                "default_target_url": default_target_url,
                "preview_url": preview_url,
                "site_check_next_at": site_check_next_at,
                "site_check_attempts": site_check_attempts,
                "status": status,
                "updated_at": func.now(),
            },
        )
        .returning(DeliveryOutbox.id)
    )

    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        row = session.execute(stmt).scalar_one_or_none()
        session.commit()
        logger.info(
            "outbox_enqueue_ok job_id=%s delivery_id=%s status=%s",
            job_id,
            str(row) if row else "",
            status,
        )
        return row
    except Exception as exc:
        session.rollback()
        logger.exception("outbox_enqueue_failed job_id=%s err=%s", job_id, exc)
        raise
    finally:
        session.close()


def claim_site_check(delivery_id: Any) -> Optional[Dict[str, Any]]:
    delivery_uuid = _normalize_uuid(delivery_id)
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        stmt = (
            update(DeliveryOutbox)
            .where(
                DeliveryOutbox.id == delivery_uuid,
                DeliveryOutbox.status == "WAITING_FOR_SITE",
            )
            .values(
                status="CHECKING_SITE",
                updated_at=func.now(),
            )
            .returning(
                DeliveryOutbox.id,
                DeliveryOutbox.job_id,
                DeliveryOutbox.client_name,
                DeliveryOutbox.payload_s3_key,
                DeliveryOutbox.default_target_url,
                DeliveryOutbox.override_target_url,
                DeliveryOutbox.preview_url,
                DeliveryOutbox.site_check_attempts,
                DeliveryOutbox.site_check_next_at,
                DeliveryOutbox.status,
            )
        )
        row = session.execute(stmt).mappings().first()
        session.commit()
        return dict(row) if row else None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def mark_site_ready(delivery_id: Any) -> bool:
    delivery_uuid = _normalize_uuid(delivery_id)
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        stmt = (
            update(DeliveryOutbox)
            .where(DeliveryOutbox.id == delivery_uuid, DeliveryOutbox.status == "CHECKING_SITE")
            .values(
                status="READY_TO_SEND",
                site_check_next_at=None,
                last_error=None,
                updated_at=func.now(),
            )
        )
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def mark_site_check_failed(
    delivery_id: Any,
    *,
    next_check_at: datetime | None,
    attempts: int,
    error_message: Any,
) -> bool:
    delivery_uuid = _normalize_uuid(delivery_id)
    err = _clean_str(error_message)[:2000]
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        stmt = (
            update(DeliveryOutbox)
            .where(DeliveryOutbox.id == delivery_uuid, DeliveryOutbox.status == "CHECKING_SITE")
            .values(
                status="WAITING_FOR_SITE",
                site_check_next_at=next_check_at,
                site_check_attempts=attempts,
                last_error=err,
                updated_at=func.now(),
            )
        )
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _normalize_uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def claim_delivery(delivery_id: Any) -> Optional[Dict[str, Any]]:
    delivery_uuid = _normalize_uuid(delivery_id)
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        stmt = (
            update(DeliveryOutbox)
            .where(
                DeliveryOutbox.id == delivery_uuid,
                DeliveryOutbox.status.in_(READY_STATUSES),
            )
            .values(
                status="SENDING",
                attempt_count=DeliveryOutbox.attempt_count + 1,
                last_error=None,
                updated_at=func.now(),
            )
            .returning(
                DeliveryOutbox.id,
                DeliveryOutbox.job_id,
                DeliveryOutbox.client_name,
                DeliveryOutbox.payload_s3_key,
                DeliveryOutbox.default_target_url,
                DeliveryOutbox.override_target_url,
                DeliveryOutbox.preview_url,
                DeliveryOutbox.status,
                DeliveryOutbox.attempt_count,
            )
        )
        row = session.execute(stmt).mappings().first()
        session.commit()
        return dict(row) if row else None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def mark_delivery_sent(delivery_id: Any) -> bool:
    delivery_uuid = _normalize_uuid(delivery_id)
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        stmt = (
            update(DeliveryOutbox)
            .where(DeliveryOutbox.id == delivery_uuid, DeliveryOutbox.status == "SENDING")
            .values(
                status="SENT",
                sent_at=func.now(),
                last_error=None,
                updated_at=func.now(),
            )
        )
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def mark_delivery_failed(delivery_id: Any, error_message: Any) -> bool:
    delivery_uuid = _normalize_uuid(delivery_id)
    err = _clean_str(error_message)[:2000]
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        stmt = (
            update(DeliveryOutbox)
            .where(DeliveryOutbox.id == delivery_uuid, DeliveryOutbox.status == "SENDING")
            .values(
                status="FAILED",
                last_error=err,
                updated_at=func.now(),
            )
        )
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
