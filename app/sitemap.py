from __future__ import annotations
from typing import Any, Dict, List, Optional

from .openai_sitemap import generate_sitemap_streaming


async def generate_sitemap(
    metadata: Dict[str, Any],
    user_data: Dict[str, Any],
    log_lines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    payload = {"metadata": metadata, "userdata": user_data}
    out = await generate_sitemap_streaming(payload, log_lines=log_lines)

    return out["sitemap_data"]
