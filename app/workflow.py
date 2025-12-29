import asyncio
import os
from typing import Dict, Any

from .sitemap import generate_sitemap
from .openai_copy import generate_page_with_retries
from .compile import compile_final
from .s3_upload import datetime_cst_stamp, upload_sitemap, upload_copy

MAX_CONCURRENT_PAGES = int(os.getenv("MAX_CONCURRENT_PAGES", "8"))

async def run_workflow(webhook_payload: Dict[str, Any], job_id: str | None = None) -> Dict[str, Any]:
    metadata = webhook_payload.get("metadata") or {}
    userdata = webhook_payload.get("userdata") or {}

    stamp = datetime_cst_stamp()

    sitemap_data = webhook_payload.get("sitemap_data")
    if not sitemap_data:
        sitemap_data = await generate_sitemap(metadata, userdata)

    try:
        upload_sitemap(metadata, sitemap_data, stamp)
    except Exception:
        pass

    pages = [
        row for row in sitemap_data.get("rows", [])
        if isinstance(row, dict) and row.get("generative_content") is True
    ]

    sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)

    async def run_page(page):
        async with sem:
            return await generate_page_with_retries({
                "metadata": metadata,
                "userdata": userdata,
                "sitemap_data": sitemap_data,
                "this_page": page
            })

    results = await asyncio.gather(*(run_page(p) for p in pages))
    envelopes = [r for r in results if r is not None]

    final_copy = compile_final(envelopes)

    try:
        upload_copy(metadata, final_copy, stamp)
    except Exception:
        pass

    return final_copy
