from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .client_identity import build_client_key
from .db_models import JobCopy, JobInput
from .delivery_schemas import DeliveryVersionOption

MAX_DELIVERY_VERSIONS = 20


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _version_label(job_id: str, updated_at: datetime | None) -> str:
    ts = _as_utc(updated_at)
    if ts is None:
        return f"{job_id}"
    return f"{ts.strftime('%Y-%m-%d %H:%M:%S UTC')} · {job_id}"


def delivery_client_key(session: Session, *, job_id: Any, client_name: Any) -> str:
    job_id_str = str(job_id or "").strip()
    if job_id_str:
        stored = session.execute(select(JobInput.client_key).where(JobInput.job_id == job_id_str)).scalar_one_or_none()
        if stored:
            return str(stored).strip()
    return build_client_key(client_name=client_name, business_domain="")


def list_version_options_for_client(
    session: Session,
    *,
    client_key: str,
    limit: int = MAX_DELIVERY_VERSIONS,
) -> list[DeliveryVersionOption]:
    key = str(client_key or "").strip()
    if not key:
        return []
    safe_limit = max(1, min(int(limit or MAX_DELIVERY_VERSIONS), MAX_DELIVERY_VERSIONS))
    stmt = (
        select(JobCopy.job_id, JobCopy.updated_at)
        .where(JobCopy.client_key == key)
        .order_by(JobCopy.updated_at.desc(), JobCopy.job_id.desc())
        .limit(safe_limit)
    )
    rows = session.execute(stmt).all()
    out: list[DeliveryVersionOption] = []
    for idx, row in enumerate(rows):
        job_id = str(row[0] or "").strip()
        updated_at = row[1] if isinstance(row[1], datetime) else None
        if not job_id:
            continue
        out.append(
            DeliveryVersionOption(
                job_id=job_id,
                label=_version_label(job_id, updated_at),
                updated_at=_as_utc(updated_at),
                is_latest=idx == 0,
            )
        )
    return out


def resolve_requested_version_job_id(
    session: Session,
    *,
    client_key: str,
    version_job_id: str,
) -> tuple[bool, bool]:
    """
    Returns tuple:
    - exists: whether the requested job copy exists.
    - belongs_to_client: whether it belongs to the provided client_key.
    """
    target_job_id = str(version_job_id or "").strip()
    if not target_job_id:
        return False, False
    row = session.execute(select(JobCopy.client_key).where(JobCopy.job_id == target_job_id)).scalar_one_or_none()
    if row is None:
        return False, False
    return True, str(row or "").strip() == str(client_key or "").strip()

