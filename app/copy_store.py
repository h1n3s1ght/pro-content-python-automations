from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from .db import get_sessionmaker
from .db_models import JobCopy, RecentlyDeletedJobCopy

logger = logging.getLogger(__name__)

SOFT_DELETE_HOURS_DEFAULT = 48


def upsert_job_copy(
    *,
    job_id: str,
    client_name: str,
    copy_data: Dict[str, Any],
) -> uuid.UUID | None:
    """
    Persist a compiled job copy payload in Postgres.

    Upsert by job_id so retries/resumes overwrite the same row.
    """
    stmt = (
        insert(JobCopy)
        .values(
            job_id=job_id,
            client_name=client_name,
            copy_data=copy_data or {},
        )
        .on_conflict_do_update(
            index_elements=[JobCopy.job_id],
            set_={
                "client_name": client_name,
                "copy_data": copy_data or {},
                "updated_at": func.now(),
            },
        )
        .returning(JobCopy.id)
    )

    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        row_id = session.execute(stmt).scalar_one_or_none()
        session.commit()
        logger.info("job_copy_upsert_ok job_id=%s copy_id=%s", job_id, str(row_id) if row_id else "")
        return row_id
    except Exception as exc:
        session.rollback()
        logger.exception("job_copy_upsert_failed job_id=%s err=%s", job_id, exc)
        raise
    finally:
        session.close()


def get_job_copy_data(job_id: str) -> Dict[str, Any] | None:
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        row = session.execute(select(JobCopy.copy_data).where(JobCopy.job_id == job_id)).scalar_one_or_none()
        return row if isinstance(row, dict) else None
    finally:
        session.close()


def soft_delete_job_copy(
    *,
    job_id: str,
    destroy_after: datetime | None = None,
) -> uuid.UUID | None:
    """
    Move a job copy into the recently-deleted table (soft delete).

    Returns the recently-deleted row id when successful, or None if the job copy does not exist.
    """
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        row = session.execute(select(JobCopy).where(JobCopy.job_id == job_id)).scalar_one_or_none()
        if row is None:
            return None

        now = datetime.now(timezone.utc)
        destroy_at = destroy_after or (now + timedelta(hours=SOFT_DELETE_HOURS_DEFAULT))

        stmt = (
            insert(RecentlyDeletedJobCopy)
            .values(
                job_id=row.job_id,
                client_name=row.client_name,
                copy_data=row.copy_data or {},
                deleted_at=now,
                destroy_after=destroy_at,
            )
            .on_conflict_do_update(
                index_elements=[RecentlyDeletedJobCopy.job_id],
                set_={
                    "client_name": row.client_name,
                    "copy_data": row.copy_data or {},
                    "deleted_at": now,
                    "destroy_after": destroy_at,
                },
            )
            .returning(RecentlyDeletedJobCopy.id)
        )
        deleted_id = session.execute(stmt).scalar_one_or_none()
        session.execute(delete(JobCopy).where(JobCopy.job_id == job_id))
        session.commit()
        logger.info(
            "job_copy_soft_deleted job_id=%s deleted_id=%s destroy_after=%s",
            job_id,
            str(deleted_id) if deleted_id else "",
            destroy_at.isoformat(),
        )
        return deleted_id
    except Exception as exc:
        session.rollback()
        logger.exception("job_copy_soft_delete_failed job_id=%s err=%s", job_id, exc)
        raise
    finally:
        session.close()


def finalize_soft_deleted_job_copy(job_id: str) -> bool:
    """
    Permanently remove a soft-deleted job copy record from Postgres.

    Returns True if the row was deleted or doesn't exist.
    """
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        row = session.execute(
            select(RecentlyDeletedJobCopy).where(RecentlyDeletedJobCopy.job_id == job_id)
        ).scalar_one_or_none()
        if row is None:
            return True
        now = datetime.now(timezone.utc)
        if row.destroy_after and row.destroy_after > now:
            # Not due yet; caller can re-schedule.
            return False
        session.execute(delete(RecentlyDeletedJobCopy).where(RecentlyDeletedJobCopy.job_id == job_id))
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_job_copies(
    *,
    client_substring: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[JobCopy], int]:
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        filters: Iterable = []
        if client_substring:
            filters = [JobCopy.client_name.ilike(f"%{client_substring}%")]

        count_stmt = select(func.count()).select_from(JobCopy)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = session.execute(count_stmt).scalar_one()

        stmt = select(JobCopy).order_by(JobCopy.created_at.desc())
        if filters:
            stmt = stmt.where(*filters)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        rows = session.execute(stmt).scalars().all()
        return rows, int(total or 0)
    finally:
        session.close()


def list_recently_deleted_job_copies(
    *,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[RecentlyDeletedJobCopy], int]:
    session_factory = get_sessionmaker()
    session = session_factory()
    try:
        count_stmt = select(func.count()).select_from(RecentlyDeletedJobCopy)
        total = session.execute(count_stmt).scalar_one()

        stmt = select(RecentlyDeletedJobCopy).order_by(RecentlyDeletedJobCopy.deleted_at.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        rows = session.execute(stmt).scalars().all()
        return rows, int(total or 0)
    finally:
        session.close()

