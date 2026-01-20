from __future__ import annotations

import os
import re
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert

from .db import get_sessionmaker
from .db_models import DeliveryOutbox

_SLUG_RE = re.compile(r"[^a-z0-9]+")
READY_STATUSES = ("READY", "READY_TO_SEND", "FAILED", "COMPLETED_PENDING_SEND")


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def slugify(value: Any) -> str:
    text = _clean_str(value).lower()
    text = _SLUG_RE.sub("-", text)
    return text.strip("-")


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


def enqueue_delivery_outbox(
    *,
    job_id: str,
    client_name: str,
    payload_s3_key: str,
    default_target_url: str,
    status: str = "COMPLETED_PENDING_SEND",
) -> None:
    stmt = (
        insert(DeliveryOutbox)
        .values(
            job_id=job_id,
            client_name=client_name,
            payload_s3_key=payload_s3_key,
            default_target_url=default_target_url,
            status=status,
        )
        .on_conflict_do_update(
            index_elements=[DeliveryOutbox.job_id],
            set_={
                "client_name": client_name,
                "payload_s3_key": payload_s3_key,
                "default_target_url": default_target_url,
                "status": status,
                "updated_at": func.now(),
            },
        )
    )

    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        session.execute(stmt)
        session.commit()
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
