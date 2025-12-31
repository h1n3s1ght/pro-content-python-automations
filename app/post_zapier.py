from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import httpx


ZAPIER_WEBHOOK_URL = os.getenv(
    "ZAPIER_WEBHOOK_URL",
    "https://hooks.zapier.com/hooks/catch/23529934/uwulu5a/",
).strip()


async def post_final_copy(
    *,
    final_copy: Dict[str, Any],
    business_name: str,
    business_domain: str,
    querystring: Optional[Dict[str, Any]] = None,
) -> tuple[bool, str]:
    if not ZAPIER_WEBHOOK_URL:
        return False, "missing_zapier_url"

    payload = {
        "data": {
            "content": final_copy,
        },
        "metadata": {
            "businessName": (business_name or "").strip(),
            "domainName": (business_domain or "").strip(),
        },
        "querystring": querystring or {},
    }

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            ZAPIER_WEBHOOK_URL,
            content=body,
            headers={"Content-Type": "application/json"},
        )

    if 200 <= resp.status_code < 300:
        return True, f"posted:{resp.status_code}"

    resp_body = (resp.text or "")[:600]
    return False, f"{resp.status_code}:{resp_body}"
