from __future__ import annotations
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .models import FinalCopyOutput, HomePayload, AboutPayload, SEOPageItem, UtilityAboutItem


def compile_final(page_envelopes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Combines individual envelopes into the exact final structure:
    { home, about, seo_pages, utility_pages }
    Missing pages remain defaults.
    """
    final = FinalCopyOutput()

    for env in page_envelopes:
        if not env:
            continue
        kind = env.get("page_kind")
        if kind == "home" and "home" in env:
            final.home = HomePayload.model_validate(env["home"])
        elif kind == "about" and "about" in env:
            final.about = AboutPayload.model_validate(env["about"])
        elif kind == "seo_page" and "seo_page" in env:
            final.seo_pages.append(SEOPageItem.model_validate(env["seo_page"]))
        elif kind == "utility_page" and "utility_page" in env:
            final.utility_pages.append(UtilityAboutItem.model_validate(env["utility_page"]))
        else:
            # skip or unknown
            pass

    # Enforce final strict schema
    return final.model_dump()
