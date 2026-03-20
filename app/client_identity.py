from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _extract_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(payload, dict):
        raw = payload.get("metadata")
        if isinstance(raw, dict):
            return raw
    return {}


def extract_business_name(metadata: dict[str, Any] | None) -> str:
    data = metadata or {}
    v = (
        data.get("business_name")
        or data.get("businessName")
        or data.get("business_name_sanitized")
        or ""
    )
    return _clean_str(v)


def extract_business_domain(metadata: dict[str, Any] | None) -> str:
    data = metadata or {}
    v = (
        data.get("domainName")
        or data.get("domain_name")
        or data.get("businessDomain")
        or data.get("business_domain")
        or data.get("domain")
        or ""
    )
    return _clean_str(v)


def normalize_business_domain(value: Any) -> str:
    raw = _clean_str(value).lower()
    if not raw:
        return ""

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        host = raw.split("/", 1)[0].split(":", 1)[0].strip().lower()

    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_client_name_for_key(value: Any) -> str:
    text = _clean_str(value).lower()
    text = _NON_ALNUM_RE.sub("", text)
    return text.strip()


def build_client_key(*, client_name: Any, business_domain: Any) -> str:
    domain = normalize_business_domain(business_domain)
    if domain:
        return domain
    name = normalize_client_name_for_key(client_name)
    return name


def extract_client_identity(payload: dict[str, Any] | None) -> tuple[str, str, str]:
    metadata = _extract_metadata(payload)
    client_name = extract_business_name(metadata)
    business_domain = extract_business_domain(metadata)
    client_key = build_client_key(client_name=client_name, business_domain=business_domain)
    return client_name, business_domain, client_key

