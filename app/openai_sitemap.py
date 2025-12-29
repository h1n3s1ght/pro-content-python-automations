from __future__ import annotations
import json
import os
import asyncio
from typing import Any, Dict, Optional

from openai import OpenAI, AssistantEventHandler
from typing_extensions import override
from pydantic import BaseModel, ConfigDict, Field, ValidationError

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
PRO_SITEMAP_ASSISTANT_ID = os.environ["PRO_SITEMAP_ASSISTANT_ID"]

client = OpenAI(api_key=OPENAI_API_KEY)


# -------------------------
# Strict sitemap schema (minimal, matches what you use)
# Expand if you want to validate every row field strictly.
# -------------------------
class SitemapMetaCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_rows: int = 0
    counted_pages: int = 0
    excluded_pages: int = 0

class SitemapMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_name_sanitized: str = ""
    service_type: str = ""
    locale: str = "en-US"
    counts: SitemapMetaCounts = Field(default_factory=SitemapMetaCounts)
    budget_ok: bool = False
    validation_passed: bool = False

class SitemapRow(BaseModel):
    # Allow extra for now, since your sitemap rows are wide.
    # If you want ultimate strictness, set extra="forbid" and list all fields.
    model_config = ConfigDict(extra="allow")
    path: str = ""
    generative_content: bool = False
    content_page_type: str = ""
    page_title: str = ""
    html_title: str = ""
    meta_description: str = ""

class SitemapData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = ""
    meta: SitemapMeta = Field(default_factory=SitemapMeta)
    headers: list[str] = Field(default_factory=list)
    rows: list[SitemapRow] = Field(default_factory=list)

class SitemapAssistantOutput(BaseModel):
    # Your existing agent output wrapped sitemap_data under a key.
    # Keep this wrapper so you can evolve without breaking.
    model_config = ConfigDict(extra="forbid")
    sitemap_data: SitemapData = Field(default_factory=SitemapData)


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


def run_sitemap_streaming_blocking(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sends metadata/userdata to Sitemap assistant, streams JSON output,
    validates it, returns as dict.
    """
    thread = client.beta.threads.create()

    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
    )

    handler = JSONCollector()
    with client.beta.threads.runs.stream(
        thread_id=thread.id,
        assistant_id=PRO_SITEMAP_ASSISTANT_ID,
        event_handler=handler,
    ) as stream:
        stream.until_done()

    raw = handler.text()
    data = json.loads(raw)

    # Strict validate wrapper -> sitemap_data
    validated = SitemapAssistantOutput.model_validate(data)
    return json.loads(validated.model_dump_json())


async def generate_sitemap_streaming(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(run_sitemap_streaming_blocking, payload)
