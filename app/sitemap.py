from __future__ import annotations
from typing import Any, Dict

from .openai_sitemap import generate_sitemap_streaming


async def generate_sitemap(metadata: Dict[str, Any], userdata: Dict[str, Any]) -> Dict[str, Any]:
    payload = {"metadata": metadata, "userdata": userdata}
    out = await generate_sitemap_streaming(payload)

    return out["sitemap_data"]
