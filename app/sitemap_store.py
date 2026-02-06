from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from .db import get_sessionmaker
from .db_models import JobSitemap

logger = logging.getLogger(__name__)


def upsert_job_sitemap(
    *,
    job_id: str,
    client_name: str,
    stamp: str | None,
    source: str,
    sitemap_data: Dict[str, Any],
) -> uuid.UUID | None:
    """
    Persist a job sitemap in Postgres.

    We upsert by job_id to avoid duplicates across retries/resumes.
    """
    rows_count = len(list((sitemap_data or {}).get("rows") or []))

    stmt = (
        insert(JobSitemap)
        .values(
            job_id=job_id,
            client_name=client_name,
            source=source,
            stamp=stamp,
            rows_count=rows_count,
            sitemap_data=sitemap_data or {},
        )
        .on_conflict_do_update(
            index_elements=[JobSitemap.job_id],
            set_={
                "client_name": client_name,
                "source": source,
                "stamp": stamp,
                "rows_count": rows_count,
                "sitemap_data": sitemap_data or {},
                "updated_at": func.now(),
            },
        )
        .returning(JobSitemap.id)
    )

    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        row = session.execute(stmt).scalar_one_or_none()
        session.commit()
        logger.info(
            "sitemap_db_upsert_ok job_id=%s sitemap_id=%s rows=%s source=%s",
            job_id,
            str(row) if row else "",
            rows_count,
            source,
        )
        return row
    except Exception as exc:
        session.rollback()
        logger.exception("sitemap_db_upsert_failed job_id=%s err=%s", job_id, exc)
        raise
    finally:
        session.close()

