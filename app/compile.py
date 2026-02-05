from __future__ import annotations
import logging
import re
from typing import Any, Dict, List

from .models import (
    FinalCopyOutput,
    HomePayload,
    AboutPayload,
    SEOPageItem,
    UtilityPageOutput,
)

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


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


def _sanitize_page_title(payload: Dict[str, Any]) -> None:
    if "page_title" in payload and not isinstance(payload.get("page_title"), str):
        payload.pop("page_title", None)


def _drop_page_title_if_none(container: Dict[str, Any]) -> None:
    if "page_title" in container and container.get("page_title") is None:
        container.pop("page_title", None)


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


def _is_utility_payload(payload: Dict[str, Any]) -> bool:
    cpt = _clean_str(payload.get("content_page_type"))
    return cpt in {"about-why", "about-team"}


def _extract_utility_source(env: Dict[str, Any]) -> tuple[Dict[str, Any], str] | None:
    kind = _clean_str(env.get("page_kind"))
    if kind == "utility_page":
        src = env.get("utility_page") if isinstance(env.get("utility_page"), dict) else env
        page_path = _string_or_empty(src.get("path")) or _string_or_empty(env.get("path"))
        return src, page_path
    if _is_utility_payload(env):
        src = env
        page_path = _string_or_empty(src.get("path"))
        return src, page_path
    utility_payload = env.get("utility_page")
    if isinstance(utility_payload, dict) and _is_utility_payload(utility_payload):
        page_path = _string_or_empty(utility_payload.get("path")) or _string_or_empty(env.get("path"))
        return utility_payload, page_path
    return None


def _slug_from_path(page_path: str) -> str:
    if not page_path:
        return ""
    trimmed = page_path.strip("/")
    if not trimmed:
        return ""
    return trimmed.split("/")[-1]


def _derive_utility_slug(page_path: str, page_title: str) -> str:
    slug = _slug_from_path(page_path)
    if slug:
        return slug
    return _slugify(page_title)


def _derive_positioning_subtitle(page_title: str) -> str:
    if _non_empty_str(page_title):
        return f"About {page_title.strip()}"
    return ""


def _build_about_content(src: Dict[str, Any], page_title: str) -> Dict[str, Any]:
    hero = src.get("about_hero")
    hero = hero if isinstance(hero, dict) else {}
    guide = src.get("about_guide")
    guide = guide if isinstance(guide, dict) else {}
    values = src.get("about_values")
    values = values if isinstance(values, dict) else {}

    hero_title = _string_or_empty(hero.get("title"))
    hero_content = _string_or_empty(hero.get("content"))
    guide_content = _string_or_empty(guide.get("content"))
    values_subtitle = _string_or_empty(values.get("subtitle"))

    title = hero_title if _non_empty_str(hero_title) else page_title
    if _non_empty_str(values_subtitle):
        subtitle = values_subtitle
    else:
        subtitle = _derive_positioning_subtitle(page_title)

    if hero_content and guide_content:
        content = f"{hero_content}\n\n{guide_content}"
    else:
        content = hero_content or guide_content

    return {
        "title": title,
        "subtitle": subtitle,
        "content": content,
    }


def _build_about_values(src: Dict[str, Any]) -> Dict[str, Any]:
    values = src.get("about_values")
    values = values if isinstance(values, dict) else {}
    raw_items = values.get("about_values_content")
    items: List[Dict[str, Any]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                heading = item.get("heading")
                content = item.get("content")
                items.append(
                    {
                        "heading": heading if isinstance(heading, str) else "",
                        "content": content if isinstance(content, str) else "",
                    }
                )
            if len(items) == 4:
                break
    while len(items) < 4:
        items.append({"heading": "", "content": ""})

    return {
        "title": _string_or_empty(values.get("title")),
        "subtitle": _string_or_empty(values.get("subtitle")),
        "about_values_content": items,
    }


def _build_about_cta(src: Dict[str, Any]) -> Dict[str, Any]:
    cta = src.get("about_cta")
    cta = cta if isinstance(cta, dict) else {}
    return {
        "title": _string_or_empty(cta.get("title")),
        "content": _string_or_empty(cta.get("content")),
    }


def _extract_page_title(src: Dict[str, Any]) -> str:
    raw_title = src.get("page_title")
    if _non_empty_str(raw_title):
        return raw_title
    about_content = src.get("about_content")
    if isinstance(about_content, dict):
        about_title = about_content.get("title")
        if _non_empty_str(about_title):
            return about_title
    hero = src.get("about_hero")
    hero = hero if isinstance(hero, dict) else {}
    hero_title = hero.get("title")
    if _non_empty_str(hero_title):
        return hero_title
    return ""


def _build_utility_page(src: Dict[str, Any], page_path: str) -> Dict[str, Any]:
    page_title = _extract_page_title(src)
    slug = _derive_utility_slug(page_path, page_title)
    about_content = src.get("about_content")
    if isinstance(about_content, dict):
        content_payload = {
            "title": _string_or_empty(about_content.get("title")),
            "subtitle": _string_or_empty(about_content.get("subtitle")),
            "content": _string_or_empty(about_content.get("content")),
        }
    else:
        content_payload = _build_about_content(src, page_title)

    about_values = src.get("about_values")
    values_payload = _build_about_values(src)
    if isinstance(about_values, dict):
        items = about_values.get("about_values_content") or []
        if not isinstance(items, list):
            items = []
        while len(items) < 4:
            items.append({"heading": "", "content": ""})
        values_payload = {
            "title": _string_or_empty(about_values.get("title")),
            "subtitle": _string_or_empty(about_values.get("subtitle")),
            "about_values_content": items[:4],
        }

    about_cta = src.get("about_cta")
    cta_payload = _build_about_cta(src)
    if isinstance(about_cta, dict):
        cta_payload = {
            "title": _string_or_empty(about_cta.get("title")),
            "content": _string_or_empty(about_cta.get("content")),
        }
    return {
        "page_id": None,
        "page_title": page_title,
        "slug": slug,
        "html_title": _string_or_empty(src.get("html_title")),
        "meta_description": _string_or_empty(src.get("meta_description")),
        "about_content": content_payload,
        "about_values": values_payload,
        "about_cta": cta_payload,
    }


def _upsert_utility_page(pages: List[UtilityPageOutput], page: UtilityPageOutput) -> None:
    for idx, existing in enumerate(pages):
        if existing.slug == page.slug:
            pages[idx] = page
            return
    pages.append(page)


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
        if isinstance(env, dict):
            utility_source = _extract_utility_source(env)
        else:
            utility_source = None
        if utility_source is not None:
            src, page_path = utility_source
            src_page_title = src.get("page_title")
            if _non_empty_str(src_page_title):
                log_path = page_path or f"/{_slugify(src_page_title)}"
                logger.info("utility_page.page_title preserved for %s", log_path)
            utility_payload = _build_utility_page(src, page_path)
            _upsert_utility_page(final.utility_pages, UtilityPageOutput.model_validate(utility_payload))
            continue
        kind = _clean_str(env.get("page_kind"))
        if kind == "home" and "home" in env:
            payload = dict(env.get("home") or {})
            _sanitize_page_title(payload)
            payload["path"] = _resolve_path(kind, env, payload)
            final.home = HomePayload.model_validate(payload)
        elif kind == "about" and "about" in env:
            payload = dict(env.get("about") or {})
            _sanitize_page_title(payload)
            payload["path"] = _resolve_path(kind, env, payload)
            final.about = AboutPayload.model_validate(payload)
        elif kind == "seo_page" and "seo_page" in env:
            payload = dict(env.get("seo_page") or {})
            _sanitize_page_title(payload)
            payload["path"] = _resolve_path(kind, env, payload)
            final.seo_pages.append(SEOPageItem.model_validate(payload))
        else:
            # skip or unknown
            pass

    if not _clean_str(final.home.path):
        final.home.path = "/"
    if not _clean_str(final.about.path):
        final.about.path = "/about"
    _validate_final_paths(final)

    # Enforce final strict schema
    output = final.model_dump()
    if isinstance(output.get("home"), dict):
        _drop_page_title_if_none(output["home"])
    if isinstance(output.get("about"), dict):
        _drop_page_title_if_none(output["about"])
    for page in output.get("seo_pages") or []:
        if isinstance(page, dict):
            _drop_page_title_if_none(page)
    return {"data": {"content": output}}
