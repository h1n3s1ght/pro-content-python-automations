from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, Optional, List

from openai import OpenAI, AssistantEventHandler
from pydantic import TypeAdapter, ValidationError
from typing_extensions import override

from .models import AssistantEnvelope

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
PRO_COPY_ASSISTANT_ID = os.getenv("PRO_COPY_ASSISTANT_ID", "").strip()

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing or empty in this container environment")
if not PRO_COPY_ASSISTANT_ID:
    raise RuntimeError("PRO_COPY_ASSISTANT_ID is missing or empty in this container environment")

EnvelopeAdapter = TypeAdapter(AssistantEnvelope)

MAX_PAGE_RETRIES = int(os.getenv("MAX_PAGE_RETRIES", "3"))
OPENAI_TRANSIENT_RETRIES = int(os.getenv("OPENAI_TRANSIENT_RETRIES", "4"))
OPENAI_BASE_BACKOFF = float(os.getenv("OPENAI_BASE_BACKOFF", "0.8"))


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


def _payload_path(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    this_page = payload.get("this_page")
    if not isinstance(this_page, dict):
        return ""
    return str(this_page.get("path") or "")


def _unwrap_content(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    inner = data.get("data")
    if isinstance(inner, dict) and "content" in inner:
        return inner.get("content")
    if isinstance(data.get("content"), dict):
        return data.get("content")
    return data


def _coerce_envelope(data: Any, payload: Dict[str, Any]) -> Any:
    data = _unwrap_content(data)
    if not isinstance(data, dict):
        return data

    if "page_kind" in data:
        if not data.get("path"):
            path = _payload_path(payload)
            if path:
                data = dict(data)
                data["path"] = path
        return data

    if "utility_page" in data:
        utility = data.get("utility_page")
        if isinstance(utility, dict):
            path = data.get("path") or utility.get("path") or _payload_path(payload)
            return {"page_kind": "utility_page", "path": path, "utility_page": utility}

    content_type = str(data.get("content_page_type") or "").strip()
    if content_type in {"about-why", "about-team"}:
        path = data.get("path") or _payload_path(payload)
        return {"page_kind": "utility_page", "path": path, "utility_page": data}

    if "home" in data:
        path = data.get("path") or _payload_path(payload) or "/"
        return {"page_kind": "home", "path": path, "home": data.get("home")}

    if "about" in data:
        path = data.get("path") or _payload_path(payload) or "/about"
        return {"page_kind": "about", "path": path, "about": data.get("about")}

    if "seo_page" in data:
        path = data.get("path") or _payload_path(payload)
        return {"page_kind": "seo_page", "path": path, "seo_page": data.get("seo_page")}

    if isinstance(data.get("fields"), dict) and (
        data.get("post_title") or data.get("post_name") or data.get("seo_page_type")
    ):
        path = data.get("path") or _payload_path(payload)
        return {"page_kind": "seo_page", "path": path, "seo_page": data}

    return data


def run_copy_streaming_blocking(
    payload: Dict[str, Any],
    log_lines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    def _log(line: str) -> None:
        if log_lines is not None:
            log_lines.append(line)
        print(line)

    client = _new_client()

    thread = client.beta.threads.create()
    _log(f"[copy] thread_id: {thread.id}")

    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=_minify_payload(payload),
    )

    handler = JSONCollector()
    with client.beta.threads.runs.stream(
        thread_id=thread.id,
        assistant_id=PRO_COPY_ASSISTANT_ID,
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
            _log(f"[copy] run_id: {run_id}")
            _log(f"[copy] thread_url: {thread_url}")
            _log(f"[copy] run_url: {run_url}")

    raw = handler.text()
    data = json.loads(raw)
    coerced = _coerce_envelope(data, payload)
    validated = EnvelopeAdapter.validate_python(coerced)
    return json.loads(validated.model_dump_json())


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
            return await asyncio.to_thread(run_copy_streaming_blocking, payload, log_lines)
        except Exception as e:
            if _is_transient_openai_error(e) and attempt < OPENAI_TRANSIENT_RETRIES:
                last_err = str(e)
                await asyncio.sleep(OPENAI_BASE_BACKOFF * attempt)
                continue
            raise
    raise RuntimeError(last_err or "unknown error")


async def generate_page_with_retries(payload, log_lines: Optional[List[str]] = None):
    last_err = None
    for attempt in range(1, MAX_PAGE_RETRIES + 1):
        try:
            return await _call_openai_with_transient_retries(payload, log_lines=log_lines)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = f"parse/validation error: {e}"
        except Exception as e:
            last_err = f"run error: {e}"
        await asyncio.sleep(0.6 * attempt)

    raise RuntimeError(f"page generation failed after {MAX_PAGE_RETRIES}: {last_err}")
