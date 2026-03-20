from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .client_identity import extract_client_identity
from .db import get_sessionmaker
from .db_models import JobInput

logger = logging.getLogger(__name__)


def upsert_job_input(
    *,
    job_id: str,
    input_payload: Dict[str, Any],
) -> uuid.UUID | None:
    payload = input_payload if isinstance(input_payload, dict) else {}
    client_name, business_domain, client_key = extract_client_identity(payload)

    stmt = (
        insert(JobInput)
        .values(
            job_id=job_id,
            client_name=client_name,
            business_domain=business_domain,
            client_key=client_key,
            input_payload=payload,
        )
        .on_conflict_do_update(
            index_elements=[JobInput.job_id],
            set_={
                "client_name": client_name,
                "business_domain": business_domain,
                "client_key": client_key,
                "input_payload": payload,
                "updated_at": func.now(),
            },
        )
        .returning(JobInput.id)
    )

    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        row_id = session.execute(stmt).scalar_one_or_none()
        session.commit()
        logger.info(
            "job_input_upsert_ok job_id=%s input_id=%s client_key=%s",
            job_id,
            str(row_id) if row_id else "",
            client_key,
        )
        return row_id
    except Exception as exc:
        session.rollback()
        logger.exception("job_input_upsert_failed job_id=%s err=%s", job_id, exc)
        raise
    finally:
        session.close()


def get_job_input_payload(job_id: str) -> Dict[str, Any] | None:
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        row = session.execute(select(JobInput.input_payload).where(JobInput.job_id == job_id)).scalar_one_or_none()
        return row if isinstance(row, dict) else None
    finally:
        session.close()

