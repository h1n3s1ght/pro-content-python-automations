from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI, AssistantEventHandler
from pydantic import TypeAdapter, ValidationError
from typing_extensions import override

from .models import CampaignPageItem

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
CAMPAIGN_ASSISTANT_ID = "asst_Px0t2QTPDdxgL3hKUWFqsHJa"

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing or empty in this container environment")

CampaignItemAdapter = TypeAdapter(CampaignPageItem)

MAX_CAMPAIGN_RETRIES = int(os.getenv("MAX_CAMPAIGN_RETRIES", os.getenv("MAX_PAGE_RETRIES", "3")))
OPENAI_TRANSIENT_RETRIES = int(os.getenv("OPENAI_TRANSIENT_RETRIES", "4"))
OPENAI_BASE_BACKOFF = float(os.getenv("OPENAI_BASE_BACKOFF", "0.8"))


class CampaignGenerationError(RuntimeError):
    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class CampaignParseError(CampaignGenerationError):
    pass


class JSONCollector(AssistantEventHandler):
    def __init__(self) -> None:
        super().__init__()
        self._buf: list[str] = []

    @override
    def on_text_delta(self, delta, snapshot):
        try:
            self._buf.append(delta.value)
        except Exception:
            pass

    def text(self) -> str:
        return "".join(self._buf).strip()


def _minify_payload(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _new_client() -> OpenAI:
    return OpenAI(
        api_key=OPENAI_API_KEY,
        default_headers={"OpenAI-Beta": "assistants=v2"},
    )


def _derive_slug_from_path(path: str) -> str:
    clean = str(path or "").strip().strip("/")
    if not clean:
        return ""
    return clean.split("/")[-1]


def _build_campaign_payload(
    *,
    metadata: Optional[Dict[str, Any]],
    user_data: Optional[Dict[str, Any]],
    job_details: Optional[Dict[str, Any]],
    sitemap_data: Optional[Dict[str, Any]],
    campaign_path: str,
    campaign_slug: str,
) -> Dict[str, Any]:
    path = str(campaign_path or "").strip()
    slug = str(campaign_slug or "").strip() or _derive_slug_from_path(path)
    payload = {
        "metadata": metadata or {},
        "user_data": user_data or {},
        "userdata": user_data or {},
        "job_details": job_details or {},
        "sitemap_data": sitemap_data or {},
        "campaign_path": path,
        "campaign_slug": slug,
    }
    return payload


def _prepare_payload(
    payload: Optional[Dict[str, Any]],
    *,
    metadata: Optional[Dict[str, Any]],
    user_data: Optional[Dict[str, Any]],
    job_details: Optional[Dict[str, Any]],
    sitemap_data: Optional[Dict[str, Any]],
    campaign_path: str,
    campaign_slug: str,
) -> Dict[str, Any]:
    if payload is None:
        return _build_campaign_payload(
            metadata=metadata,
            user_data=user_data,
            job_details=job_details,
            sitemap_data=sitemap_data,
            campaign_path=campaign_path,
            campaign_slug=campaign_slug,
        )

    merged = dict(payload)
    if metadata is not None:
        merged["metadata"] = metadata
    if user_data is not None:
        merged["user_data"] = user_data
        merged["userdata"] = user_data
    if job_details is not None:
        merged["job_details"] = job_details
    if sitemap_data is not None:
        merged["sitemap_data"] = sitemap_data
    if campaign_path:
        merged["campaign_path"] = str(campaign_path).strip()
    if campaign_slug:
        merged["campaign_slug"] = str(campaign_slug).strip()

    merged.setdefault("metadata", {})
    merged.setdefault("user_data", merged.get("userdata") or {})
    merged.setdefault("userdata", merged.get("user_data") or {})
    merged.setdefault("job_details", {})
    merged.setdefault("sitemap_data", {})
    merged["campaign_path"] = str(merged.get("campaign_path") or "").strip()
    merged["campaign_slug"] = str(merged.get("campaign_slug") or "").strip() or _derive_slug_from_path(
        merged["campaign_path"]
    )
    return merged


def _extract_content_dict(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None

    nested_data = value.get("data")
    if isinstance(nested_data, dict):
        nested_content = nested_data.get("content")
        if isinstance(nested_content, dict):
            return dict(nested_content)

    nested_content = value.get("content")
    if isinstance(nested_content, dict):
        return dict(nested_content)

    direct_keys = {"slug", "title", "subtitle", "content", "desc-content", "desc_content", "descContent"}
    if direct_keys.intersection(value.keys()):
        return dict(value)

    return None


def _collect_content_candidates(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []

    root_candidate = _extract_content_dict(data)
    if root_candidate is not None:
        candidates.append(root_candidate)

    for key in ("campaign_page", "page", "item"):
        candidate = _extract_content_dict(data.get(key))
        if candidate is not None:
            candidates.append(candidate)

    list_candidate = data.get("campaign_pages")
    if isinstance(list_candidate, list):
        for item in list_candidate:
            candidate = _extract_content_dict(item)
            if candidate is not None:
                candidates.append(candidate)

    return candidates


def _choose_campaign_candidate(
    candidates: List[Dict[str, Any]],
    *,
    campaign_slug: str,
    campaign_path: str,
) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None

    slug = str(campaign_slug or "").strip().lower()
    path = str(campaign_path or "").strip()

    if slug:
        for candidate in candidates:
            if str(candidate.get("slug") or "").strip().lower() == slug:
                return candidate

    if path:
        for candidate in candidates:
            candidate_path = str(candidate.get("path") or candidate.get("campaign_path") or "").strip()
            if candidate_path and candidate_path == path:
                return candidate

    return candidates[0]


def _normalize_campaign_content(content: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    path = str(payload.get("campaign_path") or "").strip()
    payload_slug = str(payload.get("campaign_slug") or "").strip() or _derive_slug_from_path(path)

    slug = str(content.get("slug") or "").strip() or payload_slug

    desc_value = content.get("desc-content")
    if desc_value is None:
        desc_value = content.get("desc_content")
    if desc_value is None:
        desc_value = content.get("descContent")

    return {
        "slug": slug,
        "title": str(content.get("title") or ""),
        "subtitle": str(content.get("subtitle") or ""),
        "content": str(content.get("content") or ""),
        "desc-content": str(desc_value or ""),
    }


def _coerce_campaign_item(data: Any, payload: Dict[str, Any]) -> Any:
    if not isinstance(data, dict):
        return data

    candidates = _collect_content_candidates(data)
    selected = _choose_campaign_candidate(
        candidates,
        campaign_slug=str(payload.get("campaign_slug") or ""),
        campaign_path=str(payload.get("campaign_path") or ""),
    )
    if selected is None:
        return data

    normalized = _normalize_campaign_content(selected, payload)
    return {"data": {"content": normalized}}


def run_campaign_streaming_blocking(
    payload: Dict[str, Any],
    log_lines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    def _log(line: str) -> None:
        if log_lines is not None:
            log_lines.append(line)
        print(line)

    client = _new_client()

    thread = client.beta.threads.create()
    _log(f"[campaign] thread_id: {thread.id}")

    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=_minify_payload(payload),
    )

    handler = JSONCollector()
    with client.beta.threads.runs.stream(
        thread_id=thread.id,
        assistant_id=CAMPAIGN_ASSISTANT_ID,
        event_handler=handler,
    ) as stream:
        stream.until_done()
        run = None
        get_final_run = getattr(stream, "get_final_run", None)
        if callable(get_final_run):
            try:
                run = get_final_run()
            except Exception:
                run = None
        run_id = getattr(run, "id", "") if run is not None else ""
        if run_id:
            thread_url = f"https://platform.openai.com/threads/{thread.id}"
            run_url = f"https://platform.openai.com/threads/{thread.id}/runs/{run_id}"
            _log(f"[campaign] run_id: {run_id}")
            _log(f"[campaign] thread_url: {thread_url}")
            _log(f"[campaign] run_url: {run_url}")

    raw = handler.text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CampaignParseError(
            "campaign response was not valid JSON",
            details={"error": str(exc), "raw": raw[:2000]},
        ) from exc

    coerced = _coerce_campaign_item(data, payload)
    try:
        validated = CampaignItemAdapter.validate_python(coerced)
    except ValidationError as exc:
        raise CampaignParseError(
            "campaign response failed schema validation",
            details={"error": str(exc), "coerced": coerced, "raw": raw[:2000]},
        ) from exc

    return json.loads(validated.model_dump_json(by_alias=True))


def _is_transient_openai_error(e: Exception) -> bool:
    name = e.__class__.__name__
    msg = str(e).lower()
    if "rate limit" in msg or "429" in msg:
        return True
    if "timeout" in msg or "timed out" in msg:
        return True
    if "502" in msg or "503" in msg or "504" in msg:
        return True
    if name in {"APITimeoutError", "RateLimitError", "APIConnectionError", "InternalServerError"}:
        return True
    return False


async def _call_openai_with_transient_retries(
    payload: Dict[str, Any],
    log_lines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    last_err: Optional[str] = None
    for attempt in range(1, OPENAI_TRANSIENT_RETRIES + 1):
        try:
            return await asyncio.to_thread(run_campaign_streaming_blocking, payload, log_lines)
        except Exception as exc:
            if _is_transient_openai_error(exc) and attempt < OPENAI_TRANSIENT_RETRIES:
                last_err = str(exc)
                await asyncio.sleep(OPENAI_BASE_BACKOFF * attempt)
                continue
            raise
    raise CampaignGenerationError("campaign_openai_transient_retries_exhausted", details={"error": last_err or ""})


async def generate_campaign_page_with_retries(
    payload: Optional[Dict[str, Any]] = None,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    user_data: Optional[Dict[str, Any]] = None,
    job_details: Optional[Dict[str, Any]] = None,
    sitemap_data: Optional[Dict[str, Any]] = None,
    campaign_path: str = "",
    campaign_slug: str = "",
    log_lines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    resolved_payload = _prepare_payload(
        payload,
        metadata=metadata,
        user_data=user_data,
        job_details=job_details,
        sitemap_data=sitemap_data,
        campaign_path=campaign_path,
        campaign_slug=campaign_slug,
    )

    last_err = ""
    last_details: Dict[str, Any] = {}

    for attempt in range(1, MAX_CAMPAIGN_RETRIES + 1):
        try:
            return await _call_openai_with_transient_retries(resolved_payload, log_lines=log_lines)
        except CampaignParseError as exc:
            last_err = f"parse/validation error: {exc}"
            last_details = dict(exc.details or {})
        except Exception as exc:
            last_err = f"run error: {exc}"
            if isinstance(exc, CampaignGenerationError):
                last_details = dict(exc.details or {})
            else:
                last_details = {}
        await asyncio.sleep(0.6 * attempt)

    raise CampaignGenerationError(
        f"campaign generation failed after {MAX_CAMPAIGN_RETRIES}: {last_err}",
        details={
            "last_error": last_err,
            "last_details": last_details,
            "campaign_path": resolved_payload.get("campaign_path", ""),
            "campaign_slug": resolved_payload.get("campaign_slug", ""),
        },
    )
