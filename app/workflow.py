from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from .sitemap import generate_sitemap
from .openai_copy import generate_page_with_retries
from .compile import compile_final
from .s3_upload import datetime_cst_stamp
from .payload_store import save_payload_json
from .sitemap_store import upsert_job_sitemap
from .errors import OperationCanceled, PauseRequested
from .logging_utils import log_info, log_debug, log_warn, log_error
from .storage import get_progress, set_progress, is_canceled, is_paused
from .outbox import build_default_target_url, build_preview_url, condense_name, enqueue_delivery_outbox

MAX_CONCURRENT_PAGES = int(os.getenv("MAX_CONCURRENT_PAGES", "4"))
PAGE_TIMEOUT_SECONDS = int(os.getenv("PAGE_TIMEOUT_SECONDS", "240"))
logger = logging.getLogger(__name__)

NON_GENERATIVE_PATHS = {
    "/contact-us",
    "/contact-thank-you",
}


async def _ensure_can_continue(job_id: Optional[str]) -> None:
    if not job_id:
        return
    if await is_canceled(job_id):
        raise OperationCanceled("job canceled by user")
    if await is_paused(job_id):
        raise PauseRequested("job paused by user")


def _extract_business_name(metadata: Dict[str, Any]) -> str:
    v = (
        metadata.get("business_name")
        or metadata.get("businessName")
        or metadata.get("business_name_sanitized")
        or ""
    )
    return str(v).strip()


def _extract_business_domain(metadata: Dict[str, Any]) -> str:
    v = (
        metadata.get("domainName")
        or metadata.get("domain_name")
        or metadata.get("businessDomain")
        or metadata.get("business_domain")
        or metadata.get("domain")
        or ""
    )
    return str(v).strip()


async def _merge_progress(job_id: str, patch: Dict[str, Any]) -> None:
    cur = await get_progress(job_id) or {}
    cur.update(patch)
    await set_progress(job_id, cur)


async def run_workflow(webhook_payload: Dict[str, Any], job_id: Optional[str] = None) -> Dict[str, Any]:
    metadata = webhook_payload.get("metadata") or {}
    user_data = (
        webhook_payload.get("user_data")
        or webhook_payload.get("userdata")
        or webhook_payload.get("userData")
        or {}
    )
    job_details = webhook_payload.get("job_details") or webhook_payload.get("jobDetails") or {}
    stamp = datetime_cst_stamp()

    business_name = _extract_business_name(metadata)
    business_domain = _extract_business_domain(metadata)
    job_id_value = job_id or ""
    client_name = business_name or business_domain or job_id_value

    async def log_i(msg: str) -> None:
        if job_id:
            await log_info(job_id, msg)

    async def log_d(msg: str) -> None:
        if job_id:
            await log_debug(job_id, msg)

    async def log_w(msg: str) -> None:
        if job_id:
            await log_warn(job_id, msg)

    async def log_e(msg: str) -> None:
        if job_id:
            await log_error(job_id, msg)

    async def prog(patch: Dict[str, Any]) -> None:
        if job_id:
            await _merge_progress(job_id, patch)

    await _ensure_can_continue(job_id)

    await log_i(f"meta_business_name: {business_name or 'MISSING'}")
    await log_i(f"meta_business_domain: {business_domain or 'MISSING'}")

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
    await _ensure_can_continue(job_id)

    sitemap_data = webhook_payload.get("sitemap_data")
    sitemap_log_lines: List[str] = []
    sitemap_source = "generated"
    if not sitemap_data:
        sitemap_data = await generate_sitemap(
            metadata=metadata,
            user_data=user_data,
            log_lines=sitemap_log_lines,
        )

    if not sitemap_data:
        await log_i("sitemap_generating")
        await _ensure_can_continue(job_id)
        sitemap_task = asyncio.create_task(generate_sitemap(metadata, user_data))
        try:
            sitemap_data = await sitemap_task
            await log_i("sitemap_generated")
        except Exception as e:
            await log_e(f"sitemap_exception: {e}")
            sitemap_data = {}
    else:
        await log_i("sitemap_provided_in_payload")
        sitemap_source = "provided"

    if sitemap_log_lines:
        for line in sitemap_log_lines:
            await log_d(line)

    seo_keywords: List[str] = []

    # Store sitemap in Postgres instead of S3 (S3 credentials may not be present/valid in Render).
    try:
        if not job_id_value:
            await log_w("sitemap_db_save_skipped: missing_job_id")
        else:
            sitemap_id = await asyncio.to_thread(
                upsert_job_sitemap,
                job_id=job_id_value,
                client_name=client_name,
                stamp=stamp,
                source=sitemap_source,
                sitemap_data=sitemap_data or {},
            )
            if sitemap_id:
                await log_i(f"sitemap_saved_db: {sitemap_id}")
    except Exception as e:
        await log_w(f"sitemap_db_save_failed: {e}")

    rows: List[Dict[str, Any]] = list((sitemap_data or {}).get("rows") or [])

    generative_rows = [r for r in rows if bool(r.get("generative_content")) is True]
    pages = [r for r in generative_rows if (r.get("path") or "") not in NON_GENERATIVE_PATHS]
    skipped = len(rows) - len(pages)

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

    user_data_for_copy = dict(user_data or {})
    user_data_for_copy["seo_keywords"] = seo_keywords

    async def run_page(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        path = page.get("path", "")
        async with sem:
            await _ensure_can_continue(job_id)
            await prog({"current": path})
            await log_i(f"page_start: {path}")
            payload = {
                "metadata": metadata,
                "userdata": user_data_for_copy,
                "sitemap_data": sitemap_data,
                "this_page": page,
                "seo_keywords": seo_keywords,
            }

            env: Optional[Dict[str, Any]] = None
            copy_log_lines: List[str] = []
            try:
                env = await asyncio.wait_for(
                    generate_page_with_retries(payload, log_lines=copy_log_lines),
                    timeout=PAGE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                await log_w(f"page_timeout: {path}: {PAGE_TIMEOUT_SECONDS}s")
                env = None
            except Exception as e:
                await log_e(f"page_exception: {path}: {e}")
                env = None
            finally:
                if copy_log_lines:
                    for line in copy_log_lines:
                        await log_d(line)
                await _ensure_can_continue(job_id)

            async with lock:
                if env is None:
                    counters["failed"] += 1
                    await log_w(f"page_failed: {path}")
                else:
                    counters["done"] += 1
                    await log_i(f"page_done: {path}")
                await prog({"pages_done": counters["done"], "pages_failed": counters["failed"]})
            return env

    results = await asyncio.gather(*(run_page(p) for p in pages))
    envelopes = [r for r in results if r is not None]
    kind_counts: Dict[str, int] = {}
    utility_paths: List[str] = []
    for env in envelopes:
        kind = str((env or {}).get("page_kind") or "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if kind == "utility_page":
            path = str((env or {}).get("path") or "")
            if path:
                utility_paths.append(path)
        await _ensure_can_continue(job_id)
    await log_i(f"envelope_counts: {kind_counts}")
    if utility_paths:
        await log_i(f"utility_paths: {utility_paths}")

    await _ensure_can_continue(job_id)
    await prog({"stage": "compile"})
    final_copy = compile_final(envelopes)

    s3_key = ""
    try:
        logger.info("job=%s payload_store_start client=%s", job_id_value, client_name)
        s3_key = save_payload_json(job_id_value, final_copy)
        logger.info("job=%s payload_store_ok path=%s", job_id_value, s3_key)
        await log_i(f"payload_stored: {s3_key}")
    except Exception as e:
        logger.warning("job=%s payload_store_failed err=%s", job_id_value, e)
        await log_w(f"payload_store_failed: {e}")

    if s3_key:
        if not job_id_value:
            await log_e("outbox_skipped: missing_job_id")
        elif not client_name:
            await log_e("outbox_skipped: missing_client_name")
        else:
            try:
                condensed_source = business_name or client_name
                condensed = condense_name(condensed_source)
                if not condensed:
                    await log_w("preview_url_skipped: missing_condensed_name")
                preview_url = ""
                if condensed:
                    try:
                        preview_url = build_preview_url(condensed)
                    except Exception as e:
                        await log_w(f"preview_url_exception: {e}")

                # Delivery modes:
                # - manual: always requires user entry in /ui/deliveries (override_target_url)
                # - zapier: attempts to pre-fill the delivery URL automatically; falls back to manual entry
                # - automatic: RESERVED for future; will eventually fetch delivery URL from a DB and auto-send
                #
                # Notes for future DB-based delivery URL resolution:
                # - Create a table keyed by client identifier (e.g. business_domain, client_name, or a stable client_id)
                #   storing a "delivery_domain" like "https://example.com" or "https://foo.wp-premium-hosting.com".
                # - In DELIVERY_MODE=zapier, look up that delivery_domain and pre-fill override_target_url when present.
                # - In DELIVERY_MODE=automatic, do the same lookup, but also auto-trigger send_delivery (and potentially
                #   re-enable readiness checks) when a delivery_domain exists. If missing, leave for manual entry.
                delivery_mode = str(os.getenv("DELIVERY_MODE", "manual") or "manual").strip().lower()

                # default_target_url is required (non-null) by the DB schema. For manual/zapier deliveries we treat it
                # as a UI hint/placeholder, not necessarily the final destination.
                base_domain = str(os.getenv("PREVIEW_BASE_DOMAIN", "wp-premium-hosting.com") or "").strip()
                preview_base_url = f"https://{condensed}.{base_domain}" if (condensed and base_domain) else ""
                default_target_url = preview_base_url
                outbox_override_target_url: str | None = None

                if delivery_mode in ("zapier", "automatic"):
                    # Minimal "automatic" resolution (safe, optional):
                    # - If upstream provides a base_url/delivery_url in job_details, use it as the prefilled value.
                    # - Otherwise, leave blank so the UI prompts the user to enter one.
                    #
                    # TODO: Replace/augment this with a DB lookup (see notes above).
                    auto_url = ""
                    if isinstance(job_details, dict):
                        for key in (
                            "delivery_url",
                            "deliveryUrl",
                            "delivery_domain",
                            "deliveryDomain",
                            "base_url",
                            "baseUrl",
                            "baseURL",
                        ):
                            v = str(job_details.get(key) or "").strip()
                            if v:
                                auto_url = v.rstrip("/")
                                break
                    if auto_url:
                        outbox_override_target_url = auto_url
                elif delivery_mode == "manual":
                    # Force manual entry: do not pre-fill override_target_url.
                    pass
                elif delivery_mode == "direct":
                    # Legacy path: send directly to the WP endpoint (bypasses Zapier).
                    default_target_url = build_default_target_url(client_name, job_details)
                else:
                    await log_w(f"delivery_mode_unknown_falling_back_to_manual: {delivery_mode}")

                initial_status = "COMPLETED_PENDING_SEND"
                delivery_id = await asyncio.to_thread(
                    enqueue_delivery_outbox,
                    job_id=job_id_value,
                    client_name=client_name,
                    payload_s3_key=s3_key,
                    default_target_url=default_target_url,
                    override_target_url=outbox_override_target_url,
                    preview_url=preview_url or None,
                    site_check_next_at=None,
                    site_check_attempts=0,
                    status=initial_status,
                )
                logger.info(
                    "job=%s outbox_enqueued delivery_id=%s s3_key=%s",
                    job_id_value,
                    str(delivery_id) if delivery_id else "",
                    s3_key,
                )
                await log_i(f"outbox_enqueued: {default_target_url}")
                if outbox_override_target_url:
                    await log_i(f"delivery_url_prefilled: {outbox_override_target_url}")
                if preview_url:
                    await log_i(f"preview_url_enqueued: {preview_url}")
                if delivery_id:
                    await log_i(f"outbox_delivery_id: {delivery_id}")
            except Exception as e:
                logger.exception("job=%s outbox_exception err=%s", job_id_value, e)
                await log_e(f"outbox_exception: {e}")
    else:
        logger.warning("job=%s outbox_skipped_missing_s3_key client=%s", job_id_value, client_name)
        await log_w("outbox_skipped: missing_s3_key")

    await prog({"stage": "completed", "current": ""})
    return final_copy
