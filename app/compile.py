from __future__ import annotations
import re
from typing import Any, Dict, List

from .models import FinalCopyOutput, HomePayload, AboutPayload, SEOPageItem, UtilityAboutItem

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _slugify(value: Any) -> str:
    text = _clean_str(value).lower()
    text = _SLUG_RE.sub("-", text)
    return text.strip("-")


def _first_non_empty(*values: Any) -> str:
    for value in values:
        cleaned = _clean_str(value)
        if cleaned:
            return cleaned
    return ""


def _seo_path_prefix(seo_page_type: Any) -> str:
    t = _clean_str(seo_page_type).lower()
    mapping = {
        "service": "service",
        "seo-service": "service",
        "industry": "industry",
        "seo-industry": "industry",
        "location": "location",
        "seo-location": "location",
    }
    normalized = mapping.get(t)
    if normalized == "service":
        return "/services/"
    if normalized == "industry":
        return "/industries/"
    if normalized == "location":
        return "/locations/"
    return "/pages/"


def _derive_seo_path(payload: Dict[str, Any]) -> str:
    slug = _slugify(payload.get("post_name"))
    if not slug:
        slug = _slugify(payload.get("post_title"))
    if not slug:
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        slug = _slugify(fields.get("html_title"))
    if not slug:
        raise ValueError("seo_page missing post_name/post_title/html_title for path")
    return f"{_seo_path_prefix(payload.get('seo_page_type'))}{slug}"


def _derive_utility_path(payload: Dict[str, Any], this_page: Dict[str, Any]) -> str:
    payload_path = _clean_str(payload.get("path"))
    if payload_path:
        return payload_path
    html_title = _clean_str(payload.get("html_title"))
    page_title = _clean_str(this_page.get("page_title") or this_page.get("html_title"))
    slug = _slugify(html_title or page_title)
    if not slug:
        raise ValueError("utility_page missing html_title/page_title for path")
    return f"/about/{slug}"


def _derive_skip_path(this_page: Dict[str, Any]) -> str:
    page_title = _clean_str(this_page.get("page_title"))
    html_title = _clean_str(this_page.get("html_title"))
    slug = _slugify(page_title or html_title)
    if not slug:
        raise ValueError("skip page missing page_title/html_title for path")
    return f"/skipped/{slug}"


def _resolve_path(kind: str, env: Dict[str, Any], payload: Dict[str, Any]) -> str:
    this_page = env.get("this_page")
    if not isinstance(this_page, dict):
        this_page = {}
    env_path = _clean_str(env.get("path"))
    this_page_path = _clean_str(this_page.get("path"))
    preferred = _first_non_empty(env_path, this_page_path)
    if preferred:
        return preferred
    if kind == "home":
        return "/"
    if kind == "about":
        return "/about"
    if kind == "seo_page":
        return _derive_seo_path(payload)
    if kind == "utility_page":
        return _derive_utility_path(payload, this_page)
    if kind == "skip":
        return _derive_skip_path(this_page)
    return ""


def _validate_final_paths(final: FinalCopyOutput) -> None:
    home_path = _clean_str(final.home.path)
    if not home_path:
        raise ValueError("home page missing path")
    if home_path != "/":
        raise ValueError(f"home path must be '/', got '{final.home.path}'")
    about_path = _clean_str(final.about.path)
    if not about_path:
        raise ValueError("about page missing path")
    if about_path != "/about":
        raise ValueError(f"about path must be '/about', got '{final.about.path}'")

    seo_paths: List[str] = []
    for idx, page in enumerate(final.seo_pages):
        path = _clean_str(page.path)
        if not path:
            raise ValueError(f"seo_pages[{idx}] missing path")
        seo_paths.append(path)
    if len(seo_paths) != len(set(seo_paths)):
        duplicates = sorted({p for p in seo_paths if seo_paths.count(p) > 1})
        raise ValueError(f"duplicate seo_page paths: {duplicates}")

    for idx, page in enumerate(final.utility_pages):
        if not _clean_str(page.path):
            raise ValueError(f"utility_pages[{idx}] missing path")


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
        kind = _clean_str(env.get("page_kind"))
        if kind == "home" and "home" in env:
            payload = dict(env.get("home") or {})
            payload["path"] = _resolve_path(kind, env, payload)
            final.home = HomePayload.model_validate(payload)
        elif kind == "about" and "about" in env:
            payload = dict(env.get("about") or {})
            payload["path"] = _resolve_path(kind, env, payload)
            final.about = AboutPayload.model_validate(payload)
        elif kind == "seo_page" and "seo_page" in env:
            payload = dict(env.get("seo_page") or {})
            payload["path"] = _resolve_path(kind, env, payload)
            final.seo_pages.append(SEOPageItem.model_validate(payload))
        elif kind == "utility_page" and "utility_page" in env:
            payload = dict(env.get("utility_page") or {})
            payload["path"] = _resolve_path(kind, env, payload)
            final.utility_pages.append(UtilityAboutItem.model_validate(payload))
        else:
            # skip or unknown
            pass

    if not _clean_str(final.home.path):
        final.home.path = "/"
    if not _clean_str(final.about.path):
        final.about.path = "/about"
    _validate_final_paths(final)

    # Enforce final strict schema
    return final.model_dump()
