from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional, List

from .sitemap import generate_sitemap
from .openai_copy import generate_page_with_retries
from .compile import compile_final
from .s3_upload import datetime_cst_stamp, upload_sitemap, upload_copy
from .storage import append_log, get_progress, set_progress

MAX_CONCURRENT_PAGES = int(os.getenv("MAX_CONCURRENT_PAGES", "8"))


async def _merge_progress(job_id: str, patch: Dict[str, Any]) -> None:
    cur = await get_progress(job_id)
    cur = cur or {}
    cur.update(patch)
    await set_progress(job_id, cur)


async def run_workflow(webhook_payload: Dict[str, Any], job_id: Optional[str] = None) -> Dict[str, Any]:
    metadata = webhook_payload.get("metadata") or {}
    userdata = webhook_payload.get("userdata") or {}
    stamp = datetime_cst_stamp()

    async def log(msg: str) -> None:
        if job_id:
            await append_log(job_id, msg)

    async def prog(patch: Dict[str, Any]) -> None:
        if job_id:
            await _merge_progress(job_id, patch)

    await prog({"stage": "sitemap"})
    sitemap_data = webhook_payload.get("sitemap_data")
    if not sitemap_data:
        await log("sitemap_generating")
        sitemap_data = await generate_sitemap(metadata, userdata)
        await log("sitemap_generated")
    else:
        await log("sitemap_provided_in_payload")

    try:
        upload_sitemap(metadata, sitemap_data, stamp)
        await log("sitemap_uploaded")
    except Exception as e:
        await log(f"sitemap_upload_failed: {e}")

    rows: List[Dict[str, Any]] = list(sitemap_data.get("rows") or [])
    pages = [r for r in rows if bool(r.get("generative_content")) is True]
    skipped = max(len(rows) - len(pages), 0)

    await prog(
        {
            "stage": "copy",
            "pages_total": len(pages),
            "pages_done": 0,
            "pages_failed": 0,
            "pages_skipped": skipped,
            "current": "",
        }
    )
    await log(f"pages_selected: total={len(pages)} skipped={skipped}")

    sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
    lock = asyncio.Lock()
    counters = {"done": 0, "failed": 0}

    async def run_page(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        path = page.get("path", "")
        async with sem:
            await prog({"current": path})
            await log(f"page_start: {path}")
            payload = {
                "metadata": metadata,
                "userdata": userdata,
                "sitemap_data": sitemap_data,
                "this_page": page,
            }
            env: Optional[Dict[str, Any]] = None
            try:
                env = await generate_page_with_retries(payload)
            except Exception as e:
                await log(f"page_exception: {path}: {e}")
                env = None

            async with lock:
                if env is None:
                    counters["failed"] += 1
                    await log(f"page_failed: {path}")
                else:
                    counters["done"] += 1
                    await log(f"page_done: {path}")
                await prog(
                    {
                        "pages_done": counters["done"],
                        "pages_failed": counters["failed"],
                    }
                )
            return env

    results = await asyncio.gather(*(run_page(p) for p in pages))
    envelopes = [r for r in results if r is not None]

    await prog({"stage": "compile"})
    final_copy = compile_final(envelopes)

    try:
        upload_copy(metadata, final_copy, stamp)
        await log("copy_uploaded")
    except Exception as e:
        await log(f"copy_upload_failed: {e}")

    await prog({"stage": "workflow_done", "current": ""})
    return final_copy
