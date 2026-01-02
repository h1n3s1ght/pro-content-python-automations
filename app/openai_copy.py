from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, Optional

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


def run_copy_streaming_blocking(payload: Dict[str, Any]) -> Dict[str, Any]:
    client = _new_client()

    thread = client.beta.threads.create()

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

    raw = handler.text()
    data = json.loads(raw)
    validated = EnvelopeAdapter.validate_python(data)
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


async def _call_openai_with_transient_retries(payload: Dict[str, Any]) -> Dict[str, Any]:
    last_err: Optional[str] = None
    for attempt in range(1, OPENAI_TRANSIENT_RETRIES + 1):
        try:
            return await asyncio.to_thread(run_copy_streaming_blocking, payload)
        except Exception as e:
            if _is_transient_openai_error(e) and attempt < OPENAI_TRANSIENT_RETRIES:
                last_err = str(e)
                await asyncio.sleep(OPENAI_BASE_BACKOFF * attempt)
                continue
            raise
    raise RuntimeError(last_err or "unknown error")


async def generate_page_with_retries(payload):
    last_err = None
    for attempt in range(1, MAX_PAGE_RETRIES + 1):
        try:
            return await _call_openai_with_transient_retries(payload)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = f"parse/validation error: {e}"
        except Exception as e:
            last_err = f"run error: {e}"
        await asyncio.sleep(0.6 * attempt)

    raise RuntimeError(f"page generation failed after {MAX_PAGE_RETRIES}: {last_err}")

