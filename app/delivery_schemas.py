from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    website_tier: str = "Pro"


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


class DeliveryVersionOption(BaseModel):
    job_id: str
    label: str
    updated_at: datetime | None = None
    is_latest: bool = False


class DeliveryVersionsResponse(BaseModel):
    items: list[DeliveryVersionOption]
    default_job_id: str | None = None


class SendVersionRequest(BaseModel):
    version_job_id: str | None = None


RerunMode = Literal["without_changes", "add_changes"]


class RerunNewPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    classification: str = ""
    seo_subtype: str | None = None
    utility_subtype: str | None = None

    @field_validator("path", "title")
    @classmethod
    def _trim_required(cls, value: str) -> str:
        out = str(value or "").strip()
        if not out:
            raise ValueError("must not be empty")
        return out

    @field_validator("classification", mode="before")
    @classmethod
    def _normalize_classification(cls, value: str | None) -> str:
        return str(value or "").strip().lower()

    @field_validator("seo_subtype", mode="before")
    @classmethod
    def _normalize_seo_subtype(cls, value: str | None) -> str | None:
        out = str(value or "").strip().lower()
        return out or None

    @field_validator("utility_subtype", mode="before")
    @classmethod
    def _normalize_utility_subtype(cls, value: str | None) -> str | None:
        out = str(value or "").strip().lower()
        return out or None

    @model_validator(mode="after")
    def _validate_conditional_subtypes(self) -> "RerunNewPageInput":
        allowed_classifications = {"", "seo", "utility"}
        if self.classification not in allowed_classifications:
            raise ValueError("classification must be one of: '', 'seo', 'utility'")

        if self.classification == "seo":
            if self.seo_subtype not in {"service", "location", "industry"}:
                raise ValueError("seo_subtype is required when classification='seo'")
        if self.classification == "utility":
            if self.utility_subtype not in {"meet-the-team", "why-choose-us"}:
                raise ValueError("utility_subtype is required when classification='utility'")

        return self


class RerunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RerunMode = "without_changes"
    specific_instructions: str = ""
    new_pages: list[RerunNewPageInput] = Field(default_factory=list)
    manual_source_payload: dict[str, Any] | None = None

    @field_validator("specific_instructions", mode="before")
    @classmethod
    def _trim_specific_instructions(cls, value: str | None) -> str:
        return str(value or "").strip()

    @field_validator("manual_source_payload", mode="before")
    @classmethod
    def _normalize_manual_source_payload(cls, value: Any) -> dict[str, Any] | None:
        if value in (None, ""):
            return None
        if not isinstance(value, dict):
            raise ValueError("manual_source_payload must be a JSON object")
        return value


class RerunResponse(BaseModel):
    ok: bool
    new_job_id: str
    task_queued: bool = True
