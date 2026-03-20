from __future__ import annotations

import asyncio
import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from pydantic import ValidationError

from .delivery_schemas import RerunRequest
from .job_input_store import get_job_input_payload, upsert_job_input
from .s3_upload import find_latest_client_form_payload
from .storage import get_payload, register_job, set_payload, set_status
from .tasks import run_full_job


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_path(value: Any) -> str:
    raw = _clean_str(value).replace("\\", "/")
    if not raw:
        return "/"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    parts = [part for part in raw.split("/") if part]
    return f"/{'/'.join(parts)}".lower()


def _infer_utility_subtype(*, path: str, title: str) -> str | None:
    haystack = f"{path} {title}".lower()
    if "meet-the-team" in haystack or "meet the team" in haystack:
        return "meet-the-team"
    if "why-choose-us" in haystack or "why choose us" in haystack:
        return "why-choose-us"
    return None


def _infer_seo_subtype(*, path: str) -> str:
    p = path.lower()
    if p.startswith("/location") or "/location/" in p or p.startswith("/locations") or "/locations/" in p:
        return "location"
    if p.startswith("/industry") or "/industry/" in p or p.startswith("/industries") or "/industries/" in p:
        return "industry"
    return "service"


def _resolve_content_page_type(page: Dict[str, Any]) -> tuple[str, str, str]:
    path = _normalize_path(page.get("path"))
    title = _clean_str(page.get("title"))
    classification = _clean_str(page.get("classification")).lower()
    seo_subtype = _clean_str(page.get("seo_subtype")).lower()
    utility_subtype = _clean_str(page.get("utility_subtype")).lower()

    if classification == "seo":
        resolved_seo = seo_subtype or _infer_seo_subtype(path=path)
        return "seo", resolved_seo, f"seo-{resolved_seo}"
    if classification == "utility":
        resolved_utility = utility_subtype or _infer_utility_subtype(path=path, title=title) or "why-choose-us"
        mapping = {
            "meet-the-team": "about-team",
            "why-choose-us": "about-why",
        }
        return "utility", resolved_utility, mapping.get(resolved_utility, "about-why")

    # Auto-classify when omitted:
    # 1) explicit subtype fields
    # 2) utility pattern inference
    # 3) SEO fallback (service)
    if utility_subtype:
        mapping = {
            "meet-the-team": "about-team",
            "why-choose-us": "about-why",
        }
        return "utility", utility_subtype, mapping.get(utility_subtype, "about-why")
    if seo_subtype:
        resolved_seo = seo_subtype or "service"
        return "seo", resolved_seo, f"seo-{resolved_seo}"

    inferred_utility = _infer_utility_subtype(path=path, title=title)
    if inferred_utility:
        mapping = {
            "meet-the-team": "about-team",
            "why-choose-us": "about-why",
        }
        return "utility", inferred_utility, mapping.get(inferred_utility, "about-why")

    fallback_seo = _infer_seo_subtype(path=path)
    return "seo", fallback_seo, f"seo-{fallback_seo}"


def _normalize_added_pages(pages: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    dedup: dict[str, Dict[str, Any]] = {}
    for raw in pages:
        if not isinstance(raw, dict):
            continue
        path = _normalize_path(raw.get("path"))
        title = _clean_str(raw.get("title"))
        if not path or not title:
            continue
        classification, subtype, content_page_type = _resolve_content_page_type(raw)
        normalized = {
            "path": path,
            "title": title,
            "classification": classification,
            "content_page_type": content_page_type,
            "seo_subtype": subtype if classification == "seo" else "",
            "utility_subtype": subtype if classification == "utility" else "",
        }
        if path in dedup:
            # Replace policy: latest entry wins for duplicate paths.
            dedup.pop(path)
        dedup[path] = normalized
    return list(dedup.values())


def _parse_request_from_form_inputs(
    *,
    mode: str | None = None,
    specific_instructions: str | None = None,
    new_pages_json: str | None = None,
) -> RerunRequest | None:
    mode_str = _clean_str(mode).lower()
    has_explicit_mode = bool(mode_str)
    if not mode_str:
        mode_str = "without_changes"

    pages_raw: list[Dict[str, Any]] = []
    raw_json = _clean_str(new_pages_json)
    if raw_json:
        loaded = json.loads(raw_json)
        if not isinstance(loaded, list):
            raise ValueError("new_pages_json must decode to an array")
        pages_raw = [item for item in loaded if isinstance(item, dict)]

    instructions = _clean_str(specific_instructions)
    if not has_explicit_mode and not instructions and not pages_raw:
        return None

    return RerunRequest(
        mode=mode_str,
        specific_instructions=instructions,
        new_pages=pages_raw,
    )


def parse_rerun_request_from_form(
    *,
    mode: str | None = None,
    specific_instructions: str | None = None,
    new_pages_json: str | None = None,
) -> RerunRequest | None:
    try:
        return _parse_request_from_form_inputs(
            mode=mode,
            specific_instructions=specific_instructions,
            new_pages_json=new_pages_json,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"new_pages_json is invalid JSON: {exc}") from exc
    except ValidationError as exc:
        raise ValueError(f"invalid rerun request: {exc}") from exc


def _build_rerun_metadata(
    *,
    mode: str,
    source_job_id: str,
    source_delivery_id: str,
    specific_instructions: str,
    added_pages: list[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "mode": mode,
        "source_job_id": source_job_id,
        "source_delivery_id": source_delivery_id,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "specific_instructions": specific_instructions,
        "added_pages": added_pages,
    }


def build_rerun_payload(
    *,
    source_payload: Dict[str, Any],
    rerun_request: RerunRequest | None = None,
    source_job_id: str = "",
    source_delivery_id: str = "",
) -> Dict[str, Any]:
    payload = copy.deepcopy(source_payload if isinstance(source_payload, dict) else {})

    user_data = payload.get("user_data")
    if not isinstance(user_data, dict):
        fallback = payload.get("userdata")
        user_data = dict(fallback) if isinstance(fallback, dict) else {}
    payload["user_data"] = user_data

    mode = "without_changes"
    specific_instructions = ""
    added_pages: list[Dict[str, Any]] = []

    if rerun_request is not None:
        mode = rerun_request.mode
        if mode == "add_changes":
            specific_instructions = _clean_str(rerun_request.specific_instructions)
            added_pages = _normalize_added_pages([item.model_dump(mode="json") for item in rerun_request.new_pages])
            user_data["rerun_overrides"] = _build_rerun_metadata(
                mode=mode,
                source_job_id=source_job_id,
                source_delivery_id=source_delivery_id,
                specific_instructions=specific_instructions,
                added_pages=added_pages,
            )
        else:
            # Preserve explicit mode metadata for traceability.
            user_data["rerun_overrides"] = _build_rerun_metadata(
                mode=mode,
                source_job_id=source_job_id,
                source_delivery_id=source_delivery_id,
                specific_instructions="",
                added_pages=[],
            )
    else:
        user_data["rerun_overrides"] = _build_rerun_metadata(
            mode=mode,
            source_job_id=source_job_id,
            source_delivery_id=source_delivery_id,
            specific_instructions="",
            added_pages=[],
        )

    job_details = payload.get("job_details")
    if not isinstance(job_details, dict):
        job_details = {}
    job_details["rerun"] = {
        "mode": mode,
        "source_job_id": source_job_id,
        "source_delivery_id": source_delivery_id,
    }
    payload["job_details"] = job_details
    return payload


def queue_rerun_from_job_id(
    job_id: str,
    *,
    rerun_request: RerunRequest | None = None,
    source_delivery_id: str = "",
    client_name: str = "",
) -> str:
    source_job_id = str(job_id or "").strip()
    payload = get_job_input_payload(source_job_id)
    if not isinstance(payload, dict):
        # Fallback for jobs where job_inputs was not persisted but Redis payload still exists.
        try:
            redis_payload = asyncio.run(get_payload(source_job_id))
        except Exception:
            redis_payload = None
        if isinstance(redis_payload, dict):
            payload = redis_payload
    if not isinstance(payload, dict):
        # Final fallback: retrieve latest source payload from S3 client-form archive.
        s3_match = find_latest_client_form_payload(client_name=str(client_name or "").strip())
        if isinstance(s3_match, tuple) and len(s3_match) == 2 and isinstance(s3_match[1], dict):
            payload = s3_match[1]
    if not isinstance(payload, dict):
        display_name = str(client_name or "").strip()
        if display_name:
            raise LookupError(
                f"missing rerun source payload for job_id={source_job_id}; "
                f"also no matching S3 clientForm payload found for '{display_name}'"
            )
        raise LookupError(f"missing rerun source payload for job_id={source_job_id}")
    return queue_rerun_from_payload(
        payload,
        rerun_request=rerun_request,
        source_job_id=source_job_id,
        source_delivery_id=source_delivery_id,
    )


def queue_rerun_from_payload(
    payload: Dict[str, Any],
    *,
    rerun_request: RerunRequest | None = None,
    source_job_id: str = "",
    source_delivery_id: str = "",
) -> str:
    if not isinstance(payload, dict):
        raise ValueError("rerun payload must be an object")

    rerun_payload = build_rerun_payload(
        source_payload=payload,
        rerun_request=rerun_request,
        source_job_id=source_job_id,
        source_delivery_id=source_delivery_id,
    )

    new_job_id = str(uuid.uuid4())
    asyncio.run(register_job(new_job_id))
    asyncio.run(set_status(new_job_id, "queued"))
    asyncio.run(set_payload(new_job_id, rerun_payload))
    upsert_job_input(job_id=new_job_id, input_payload=rerun_payload)
    run_full_job.delay(new_job_id, rerun_payload)
    return new_job_id
