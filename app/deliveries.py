from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db_session
from .db_models import DeliveryOutbox
from .delivery_schemas import (
    DeliveryListResponse,
    DeliveryOutboxSchema,
    OverrideURLRequest,
    ScheduleRequest,
    SendNowResponse,
)
from .tasks import send_delivery

router = APIRouter(prefix="/deliveries", tags=["deliveries"])

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _get_delivery(session: Session, delivery_id: UUID) -> DeliveryOutbox:
    row = session.get(DeliveryOutbox, delivery_id)
    if row is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    return row


@router.get("", response_model=DeliveryListResponse)
def list_deliveries(
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    session: Session = Depends(get_db_session),
):
    filters = []
    if status:
        filters.append(DeliveryOutbox.status == status)

    count_stmt = select(func.count()).select_from(DeliveryOutbox)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = session.execute(count_stmt).scalar_one()

    stmt = select(DeliveryOutbox)
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.order_by(DeliveryOutbox.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = session.execute(stmt).scalars().all()

    return DeliveryListResponse(
        items=[DeliveryOutboxSchema.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        status_filter=status,
    )


@router.get("/{delivery_id}", response_model=DeliveryOutboxSchema)
def get_delivery(delivery_id: UUID, session: Session = Depends(get_db_session)):
    row = _get_delivery(session, delivery_id)
    return DeliveryOutboxSchema.model_validate(row)


@router.post("/{delivery_id}/override-url", response_model=DeliveryOutboxSchema)
def set_override_url(
    delivery_id: UUID,
    payload: OverrideURLRequest,
    session: Session = Depends(get_db_session),
):
    row = _get_delivery(session, delivery_id)
    row.override_target_url = payload.override_target_url
    session.commit()
    session.refresh(row)
    return DeliveryOutboxSchema.model_validate(row)


@router.post("/{delivery_id}/send-now", response_model=SendNowResponse)
def send_now(delivery_id: UUID, session: Session = Depends(get_db_session)):
    _get_delivery(session, delivery_id)
    async_result = send_delivery.delay(str(delivery_id))
    return SendNowResponse(ok=True, task_id=async_result.id)


@router.post("/{delivery_id}/mark-ready", response_model=DeliveryOutboxSchema)
def mark_ready(delivery_id: UUID, session: Session = Depends(get_db_session)):
    row = _get_delivery(session, delivery_id)
    row.status = "READY_TO_SEND"
    row.last_error = None
    session.commit()
    session.refresh(row)
    return DeliveryOutboxSchema.model_validate(row)


@router.post("/{delivery_id}/schedule", response_model=DeliveryOutboxSchema)
def schedule_delivery(
    delivery_id: UUID,
    payload: ScheduleRequest,
    session: Session = Depends(get_db_session),
):
    row = _get_delivery(session, delivery_id)
    row.scheduled_for = payload.scheduled_for
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return DeliveryOutboxSchema.model_validate(row)
