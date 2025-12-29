from __future__ import annotations
import json
import os
import asyncio
from typing import Any, Dict, Optional

from openai import OpenAI, AssistantEventHandler
from typing_extensions import override
from pydantic import TypeAdapter, ValidationError

from .models import AssistantEnvelope

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
PRO_COPY_ASSISTANT_ID = os.environ["PRO_COPY_ASSISTANT_ID"]

client = OpenAI(api_key=OPENAI_API_KEY)

# Strict adapter to validate any of the allowed envelope types
EnvelopeAdapter = TypeAdapter(AssistantEnvelope)

MAX_PAGE_RETRIES = int(os.getenv("MAX_PAGE_RETRIES", "3"))

class JSONCollector(AssistantEventHandler):
    def __init__(self) -> None:
        super().__init__()
        self._buf = []

    @override
    def on_text_delta(self, delta, snapshot):
        # Collect incremental output text
        try:
            self._buf.append(delta.value)
        except Exception:
            pass

    def text(self) -> str:
        return "".join(self._buf).strip()


def _minify_payload(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def run_copy_streaming_blocking(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Blocking function: creates thread, sends payload, streams run, returns validated dict.
    """
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

    # Parse JSON strictly
    data = json.loads(raw)

    # Validate against one of the allowed envelope schemas
    validated = EnvelopeAdapter.validate_python(data)
    # Return plain dict (for compilation)
    return json.loads(validated.model_dump_json())


async def generate_page_with_retries(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Async wrapper with retries.
    Returns validated envelope dict or None if failed after retries.
    """
    last_err: Optional[str] = None
    for attempt in range(1, MAX_PAGE_RETRIES + 1):
        try:
            return await asyncio.to_thread(run_copy_streaming_blocking, payload)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = f"parse/validation error: {e}"
        except Exception as e:
            last_err = f"run error: {e}"
        # Backoff
        await asyncio.sleep(0.6 * attempt)

    # Give up after retries
    print(f"[WARN] page generation failed after {MAX_PAGE_RETRIES} attempts: {last_err}")
    return None
