from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
from openai import OpenAI


OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
ASSISTANT_ID = (os.getenv("OPENAI_SEO_ASSISTANT_ID") or os.getenv("OPENAI_SEO_ASSISTANT_ID", "asst_xVVUucXLTmM2QxP2GDw5RrMR")).strip()
RENDER_ENDPOINT = (os.getenv("SEO_SERVICE_RENDER_ENDPOINT") or "https://api-endpoints-ougl.onrender.com/analyze").strip()


def _extract_domain(metadata: Dict[str, Any]) -> str:
    v = (
        metadata.get("domainName")
        or metadata.get("domain_name")
        or metadata.get("businessDomain")
        or metadata.get("business_domain")
        or metadata.get("domain")
        or ""
    )
    return str(v).strip()


async def _fetch_website_seo_data(domain_url: str) -> str:
    if not domain_url:
        return json.dumps({"status": "error", "message": "missing_domain_url"})

    payload = {"domain_url": domain_url}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(RENDER_ENDPOINT, json=payload)

    try:
        body = resp.json()
    except Exception:
        body = {"status": "error", "message": f"non_json_response:{resp.status_code}", "body": (resp.text or "")[:800]}

    if 200 <= resp.status_code < 300:
        return json.dumps(body)

    return json.dumps({"status": "error", "message": f"Render API returned {resp.status_code}", "body": body})


def _parse_keywords(text: str) -> List[str]:
    if not text:
        return []

    text = text.strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for k in ("keywords", "top_keywords", "seo_keywords"):
                v = obj.get(k)
                if isinstance(v, list):
                    out = [str(x).strip() for x in v if str(x).strip()]
                    return out[:5]
        if isinstance(obj, list):
            out = [str(x).strip() for x in obj if str(x).strip()]
            return out[:5]
    except Exception:
        pass

    candidates: List[str] = []

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(\d+[\).\s]+|-|\*|\u2022)\s*(.+)$", s)
        if m:
            item = m.group(2).strip()
            item = re.sub(r"\s+\-\s+.*$", "", item).strip()
            item = item.strip('"').strip("'").strip()
            if item:
                candidates.append(item)

    if not candidates:
        m = re.search(r"(?i)\bkeywords?\b\s*:\s*(.+)$", text, flags=re.MULTILINE)
        if m:
            tail = m.group(1)
            parts = [p.strip() for p in re.split(r"[,\n]", tail) if p.strip()]
            candidates.extend(parts)

    out2: List[str] = []
    seen = set()
    for c in candidates:
        c2 = c.strip()
        if not c2:
            continue
        key = c2.lower()
        if key in seen:
            continue
        seen.add(key)
        out2.append(c2)
        if len(out2) >= 5:
            break
    return out2


def _run_assistant_sync(*, assistant_id: str, user_json_input: Dict[str, Any], domain_url: str) -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        return {"ok": False, "error": "missing_openai_api_key", "keywords": [], "raw": ""}

    if not assistant_id:
        return {"ok": False, "error": "missing_assistant_id", "keywords": [], "raw": ""}

    client = OpenAI(api_key=OPENAI_API_KEY)
    thread = client.beta.threads.create()

    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=json.dumps(user_json_input),
    )

    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant_id,
    )

    started = time.time()
    raw = ""

    while True:
        if time.time() - started > 180:
            return {"ok": False, "error": "timeout_waiting_for_assistant", "keywords": [], "raw": raw}

        time.sleep(1.0)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

        if run.status == "requires_action":
            tool_calls = run.required_action.submit_tool_outputs.tool_calls
            tool_outputs = []

            for tool in tool_calls:
                if tool.function.name == "analyze_client_website":
                    try:
                        args = json.loads(tool.function.arguments or "{}")
                    except Exception:
                        args = {}
                    domain = (args.get("domain_url") or domain_url or "").strip()
                    output_data = asyncio.run(_fetch_website_seo_data(domain))
                    tool_outputs.append({"tool_call_id": tool.id, "output": output_data})

            if tool_outputs:
                client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread.id,
                    run_id=run.id,
                    tool_outputs=tool_outputs,
                )

        elif run.status == "completed":
            messages = client.beta.threads.messages.list(thread_id=thread.id)
            for msg in messages.data:
                if msg.role == "assistant":
                    try:
                        raw = msg.content[0].text.value
                    except Exception:
                        raw = ""
                    break
            keywords = _parse_keywords(raw)
            return {"ok": True, "error": "", "keywords": keywords[:5], "raw": raw}

        elif run.status in ("failed", "expired", "cancelled"):
            return {"ok": False, "error": f"assistant_run_{run.status}", "keywords": [], "raw": raw}


async def generate_seo_keywords(*, metadata: Dict[str, Any], user_data: Dict[str, Any]) -> Dict[str, Any]:
    domain_url = _extract_domain(metadata)
    user_json_input = {
        "metadata": metadata or {},
        "userdata": user_data or {},
        "domain_url": domain_url,
    }

    out = await asyncio.to_thread(
        _run_assistant_sync,
        assistant_id=ASSISTANT_ID,
        user_json_input=user_json_input,
        domain_url=domain_url,
    )
    return out
