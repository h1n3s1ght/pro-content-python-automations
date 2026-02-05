from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DeliveryOutboxSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: str
    client_name: str
    payload_s3_key: str
    default_target_url: str
    override_target_url: str | None = None
    preview_url: str | None = None
    status: str
    scheduled_for: datetime | None = None
    attempt_count: int
    site_check_attempts: int
    site_check_next_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None = None


class DeliveryListResponse(BaseModel):
    items: list[DeliveryOutboxSchema]
    page: int
    page_size: int
    total: int
    status_filter: str | None = None


class OverrideURLRequest(BaseModel):
    override_target_url: str = Field(..., min_length=1)


class ScheduleRequest(BaseModel):
    scheduled_for: datetime


class SendNowResponse(BaseModel):
    ok: bool
    task_id: str
