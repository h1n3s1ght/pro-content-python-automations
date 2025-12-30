from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional, List

from .sitemap import generate_sitemap
from .openai_copy import generate_page_with_retries
from .compile import compile_final
from .s3_upload import datetime_cst_stamp, upload_sitemap, upload_copy
from .storage import append_log, get_progress, set_progress
from .post_zapier import post_final_copy

MAX_CONCURRENT_PAGES = int(os.getenv("MAX_CONCURRENT_PAGES", "6"))

NON_GENERATIVE_PATHS = {
    "/contact-us",
    "/contact-thank-you",
}


def _extract_business_name(metadata: Dict[str, Any]) -> str:
    v = (
        (metadata or {}).get("business_name")
        or (metadata or {}).get("businessName")
        or (metadata or {}).get("business_name_sanitized")
        or (metadata or {}).get("business")
        or ""
    )
    return (str(v).strip() if v is not None else "").strip()


def _extract_business_domain(metadata: Dict[str, Any]) -> str:
    v = (
        (metadata or {}).get("businessDomain")
        or (metadata or {}).get("business_domain")
        or (metadata or {}).get("domain")
        or (metadata or {}).get("website")
        or ""
    )
    return (str(v).strip() if v is not None else "").strip()


async def _merge_progress(job_id: str, patch: Dict[str, Any]) -> None:
    cur = await get_progress(job_id)
    cur = cur or {}
    cur.update(patch)
    await set_progress(job_id, cur)


async def run_workflow(webhook_payload: Dict[str, Any], job_id: Optional[str] = None) -> Dict[str, Any]:
    metadata = webhook_payload.get("metadata") or {}
    userdata = webhook_payload.get("userdata") or {}
    stamp = datetime_cst_stamp()

    business_name = _extract_business_name(metadata)
    business_domain = _extract_business_domain(metadata)

    async def log(msg: str) -> None:
        if job_id:
            await append_log(job_id, msg)

    async def prog(patch: Dict[str, Any]) -> None:
        if job_id:
            await _merge_progress(job_id, patch)

    await log(f"meta_business_name: {business_name or 'MISSING'}")
    await log(f"meta_business_domain: {business_domain or 'MISSING'}")

    await prog(
        {
            "stage": "sitemap",
            "pages_total": 0,
            "pages_done": 0,
            "pages_failed": 0,
            "pages_skipped": 0,
            "current": "",
        }
    )

    sitemap_data = webhook_payload.get("sitemap_data")
    if not sitemap_data:
        await log("sitemap_generating")
        sitemap_data = await generate_sitemap(metadata, userdata)
        await log("sitemap_generated")
    else:
        await log("sitemap_provided_in_payload")

    try:
        s3_key = upload_sitemap(metadata, sitemap_data, stamp)
        await log(f"sitemap_uploaded: {s3_key}")
    except Exception as e:
        await log(f"sitemap_upload_failed: {e}")

    rows: List[Dict[str, Any]] = list(sitemap_data.get("rows") or [])

    generative_rows = [r for r in rows if bool(r.get("generative_content")) is True]
    excluded_forced = [r for r in generative_rows if (r.get("path") or "") in NON_GENERATIVE_PATHS]
    pages = [r for r in generative_rows if (r.get("path") or "") not in NON_GENERATIVE_PATHS]

    non_generative_count = sum(1 for r in rows if bool(r.get("generative_content")) is not True)
    skipped = non_generative_count + len(excluded_forced)

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
        s3_key = upload_copy(metadata, final_copy, stamp)
        await log(f"copy_uploaded: {s3_key}")
    except Exception as e:
        await log(f"copy_upload_failed: {e}")

    if not business_name or not business_domain:
        await log("zapier_skipped: missing_business_headers")
    else:
        try:
            ok, msg = await post_final_copy(
                final_copy=final_copy,
                business_name=business_name,
                business_domain=business_domain,
            )
            await log(f"zapier:{msg}")
        except Exception as e:
            await log(f"zapier_exception: {e}")

    await prog({"stage": "completed", "current": ""})
    return final_copy
