from __future__ import annotations

import os
from typing import Any, Dict

import httpx


ZAPIER_WEBHOOK_URL = os.getenv(
    "ZAPIER_WEBHOOK_URL",
    "https://hooks.zapier.com/hooks/catch/23529934/uwulu5a/",
).strip()


def _meta_business_name(metadata: Dict[str, Any]) -> str:
    return (
        (metadata or {}).get("businessName")
        or (metadata or {}).get("business_name")
        or (metadata or {}).get("business_name_sanitized")
        or ""
    ).strip()


def _meta_business_domain(metadata: Dict[str, Any]) -> str:
    return (
        (metadata or {}).get("businessDomain")
        or (metadata or {}).get("business_domain")
        or (metadata or {}).get("domain")
        or ""
    ).strip()


async def post_final_copy(
    *,
    final_copy: Dict[str, Any],
    metadata: Dict[str, Any],
) -> tuple[bool, str]:
    if not ZAPIER_WEBHOOK_URL:
        return False, "missing_zapier_url"

    payload = {"data": {"content": final_copy}}

    headers = {
        "Content-Type": "application/json",
        "X-Business-Domain": _meta_business_domain(metadata),
        "X-Business-Name": _meta_business_name(metadata),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(ZAPIER_WEBHOOK_URL, json=payload, headers=headers)

    if 200 <= resp.status_code < 300:
        return True, f"posted:{resp.status_code}"

    body = (resp.text or "")[:600]
    return False, f"{resp.status_code}:{body}"
