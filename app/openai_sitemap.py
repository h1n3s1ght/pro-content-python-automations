from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict

from openai import OpenAI, AssistantEventHandler
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import override

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
PRO_SITEMAP_ASSISTANT_ID = os.getenv("PRO_SITEMAP_ASSISTANT_ID", "").strip()

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing or empty in this container environment")
if not PRO_SITEMAP_ASSISTANT_ID:
    raise RuntimeError("PRO_SITEMAP_ASSISTANT_ID is missing or empty in this container environment")

client = OpenAI(
    api_key=OPENAI_API_KEY,
    default_headers={"OpenAI-Beta": "assistants=v2"},
)


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


def _minify(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _normalize_sitemap_output(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {"sitemap_data": {"version": "", "meta": {}, "headers": [], "rows": []}}

    root = dict(data)
    root.pop("name", None)

    sitemap_data = root.get("sitemap_data")
    if not isinstance(sitemap_data, dict):
        sitemap_data = root if isinstance(root.get("rows"), list) else {}

    sitemap_data = dict(sitemap_data)

    meta = sitemap_data.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    else:
        meta = dict(meta)

    meta.pop("fail_report", None)

    rows = sitemap_data.get("rows")
    if not isinstance(rows, list):
        rows = []

    counts_in = meta.get("counts")
    if isinstance(counts_in, dict):
        meta["counts"] = dict(counts_in)

    sitemap_data["meta"] = meta
    return {"sitemap_data": sitemap_data}


def run_sitemap_streaming_blocking(payload: Dict[str, Any]) -> Dict[str, Any]:
    thread = client.beta.threads.create()
    print(f"[sitemap] thread_id: {thread.id}")

    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=_minify(payload),
    )

    handler = JSONCollector()
    with client.beta.threads.runs.stream(
        thread_id=thread.id,
        assistant_id=PRO_SITEMAP_ASSISTANT_ID,
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
            print(f"[sitemap] run_id: {run_id}")
            print(f"[sitemap] thread_url: {thread_url}")
            print(f"[sitemap] run_url: {run_url}")

    raw = handler.text()
    data = json.loads(raw)
    data = _normalize_sitemap_output(data)

    validated = SitemapAssistantOutput.model_validate(data)
    return json.loads(validated.model_dump_json())


async def generate_sitemap_streaming(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(run_sitemap_streaming_blocking, payload)
