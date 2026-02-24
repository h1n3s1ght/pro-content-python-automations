from __future__ import annotations

import asyncio
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from .sitemap import generate_sitemap
from .openai_copy import generate_page_with_retries
from .openai_campaign import generate_campaign_page_with_retries
from .compile import compile_final
from .s3_upload import datetime_cst_stamp
from .copy_store import upsert_job_copy
from .payload_store import save_payload_json
from .sitemap_store import upsert_job_sitemap
from .errors import OperationCanceled, PauseRequested
from .logging_utils import log_info, log_debug, log_warn, log_error
from .storage import get_progress, set_progress, is_canceled, is_paused
from .outbox import build_default_target_url, build_preview_url, condense_name, enqueue_delivery_outbox
from .models import CampaignPageItem

MAX_CONCURRENT_PAGES = int(os.getenv("MAX_CONCURRENT_PAGES", "4"))
PAGE_TIMEOUT_SECONDS = int(os.getenv("PAGE_TIMEOUT_SECONDS", "240"))
logger = logging.getLogger(__name__)

NON_GENERATIVE_PATHS = {
    "/contact-us",
    "/contact-thank-you",
}

CAMPAIGN_PAGES: tuple[tuple[str, str], ...] = (
    ("/campaign/discoverycall", "discoverycall"),
    ("/campaign/it-buyers-guide", "it-buyers-guide"),
)


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


def _ensure_final_content_container(final_copy: Dict[str, Any]) -> Dict[str, Any]:
    data = final_copy.get("data")
    if not isinstance(data, dict):
        data = {}
        final_copy["data"] = data

    content = data.get("content")
    if not isinstance(content, dict):
        content = {}
        data["content"] = content
    return content


def _questionnaire_campaign_pages(user_data: Dict[str, Any]) -> List[tuple[str, str]]:
    """
    Placeholder extension point for questionnaire-driven campaign pages.

    TODO:
    - Read/validate `user_data.additional_campaigns` once questionnaire schema is finalized.
    - Map questionnaire entries into `(campaign_path, campaign_slug)` tuples.
    - De-duplicate against the fixed CAMPAIGN_PAGES list.
    """
    if not isinstance(user_data, dict):
        return []

    # Safe read to avoid breaking current jobs when the field is missing.
    additional_campaigns = user_data.get("additional_campaigns")
    if additional_campaigns is None:
        return []

    # Intentionally disabled until schema/requirements are finalized.
    return []


async def _generate_campaign_pages_best_effort(
    *,
    metadata: Dict[str, Any],
    user_data: Dict[str, Any],
    job_details: Dict[str, Any],
    sitemap_data: Dict[str, Any],
    job_id: Optional[str],
    log_i,
    log_d,
    log_e,
    prog,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    counters = {"done": 0, "failed": 0}
    campaign_pages_to_generate = list(CAMPAIGN_PAGES)
    campaign_pages_to_generate.extend(_questionnaire_campaign_pages(user_data))
    total = len(campaign_pages_to_generate)
    await prog(
        {
            "campaign_pages_total": total,
            "campaign_pages_done": 0,
            "campaign_pages_failed": 0,
            "campaign_current": "",
        }
    )

    for campaign_path, campaign_slug in campaign_pages_to_generate:
        await _ensure_can_continue(job_id)
        await prog({"campaign_current": campaign_path})
        await log_i(f"campaign_page_start: path={campaign_path} slug={campaign_slug}")

        campaign_log_lines: List[str] = []
        try:
            campaign_item = await generate_campaign_page_with_retries(
                metadata=metadata,
                user_data=user_data,
                job_details=job_details,
                sitemap_data=sitemap_data,
                campaign_path=campaign_path,
                campaign_slug=campaign_slug,
                log_lines=campaign_log_lines,
            )
            validated = CampaignPageItem.model_validate(campaign_item)
            results.append(validated.model_dump(by_alias=True))
            counters["done"] += 1
            await log_i(f"campaign_page_done: path={campaign_path} slug={campaign_slug}")
        except (OperationCanceled, PauseRequested):
            raise
        except Exception as e:
            counters["failed"] += 1
            tb = traceback.format_exc().strip().replace("\n", " | ")
            logger.exception(
                "job=%s campaign_page_failed path=%s slug=%s err=%s",
                job_id or "",
                campaign_path,
                campaign_slug,
                e,
            )
            await log_e(
                f"campaign_page_failed: path={campaign_path} slug={campaign_slug} "
                f"error={type(e).__name__}: {e}"
            )
            await log_e(
                f"campaign_page_traceback: path={campaign_path} slug={campaign_slug} trace={tb[:5000]}"
            )
        finally:
            if campaign_log_lines:
                for line in campaign_log_lines:
                    await log_d(line)
            await prog(
                {
                    "campaign_pages_done": counters["done"],
                    "campaign_pages_failed": counters["failed"],
                }
            )

    await prog({"campaign_current": ""})
    await log_i(
        "campaign_pages_summary: "
        f"total={total} done={counters['done']} failed={counters['failed']}"
    )
    return results


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
    campaign_pages_results = await _generate_campaign_pages_best_effort(
        metadata=metadata,
        user_data=user_data,
        job_details=job_details,
        sitemap_data=sitemap_data or {},
        job_id=job_id,
        log_i=log_i,
        log_d=log_d,
        log_e=log_e,
        prog=prog,
    )
    final_copy = compile_final(envelopes, campaign_pages=campaign_pages_results)

    payload_ref = ""
    db_copy_id = None
    try:
        if not job_id_value:
            await log_w("copy_db_save_skipped: missing_job_id")
        elif not client_name:
            await log_w("copy_db_save_skipped: missing_client_name")
        else:
            db_copy_id = await asyncio.to_thread(
                upsert_job_copy,
                job_id=job_id_value,
                client_name=client_name,
                copy_data=final_copy,
            )
            if db_copy_id:
                payload_ref = f"db:{job_id_value}"
                await log_i(f"copy_saved_db: {db_copy_id}")
    except Exception as e:
        logger.warning("job=%s copy_db_save_failed err=%s", job_id_value, e)
        await log_w(f"copy_db_save_failed: {e}")

    disk_ref = ""
    try:
        logger.info("job=%s payload_store_start client=%s", job_id_value, client_name)
        disk_ref = save_payload_json(job_id_value, final_copy)
        logger.info("job=%s payload_store_ok path=%s", job_id_value, disk_ref)
        await log_i(f"payload_stored: {disk_ref}")
    except Exception as e:
        logger.warning("job=%s payload_store_failed err=%s", job_id_value, e)
        await log_w(f"payload_store_failed: {e}")

    if disk_ref:
        payload_ref = disk_ref

    if payload_ref:
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
                    payload_s3_key=payload_ref,
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
                    payload_ref,
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
        logger.warning("job=%s outbox_skipped_missing_payload_ref client=%s", job_id_value, client_name)
        await log_w("outbox_skipped: missing_payload_ref")

    await prog({"stage": "completed", "current": ""})
    return final_copy
