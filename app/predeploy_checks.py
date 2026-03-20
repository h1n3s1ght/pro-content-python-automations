from __future__ import annotations

import os
from typing import Any, Callable, Dict
from urllib.parse import urlparse

from .s3_upload import find_latest_client_form_payload


_DEFAULT_PROBE_CLIENT_NAME = "ISAAC TESTING"
_DEFAULT_PROBE_EXPECTED_KEY = "clientForm/ISAAC_TESTING_2026-03-19T20-46-06-406Z.json"
_DEFAULT_PROBE_BUCKET = "pro-tier-bucket"
_DEFAULT_PROBE_PREFIX = "clientForm/"
_DEFAULT_PROBE_SCAN_LIMIT = 2000


def _env_bool(
    getenv: Callable[[str, str | None], str | None],
    name: str,
    default: str,
) -> bool:
    raw = str(getenv(name, default) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(
    getenv: Callable[[str, str | None], str | None],
    name: str,
    default: int,
) -> int:
    raw = str(getenv(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except Exception:
        return default
    return value if value > 0 else default


def _normalize_expected_key(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("s3://"):
        parsed = urlparse(raw)
        return str(parsed.path or "").lstrip("/")
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        path = str(parsed.path or "").lstrip("/")
        if not path:
            return ""
        host = str(parsed.netloc or "")
        if ".s3." in host:
            # Virtual-host style: bucket.s3.region.amazonaws.com/key
            return path
        # Path style: s3.region.amazonaws.com/bucket/key
        if path.count("/") >= 1:
            return path.split("/", 1)[1]
        return ""
    return raw


def run_client_form_probe(
    *,
    getenv: Callable[[str, str | None], str | None] = os.getenv,
    finder: Callable[..., tuple[str, Dict[str, Any]] | None] = find_latest_client_form_payload,
) -> tuple[bool, str]:
    if not _env_bool(getenv, "PREDEPLOY_S3_PROBE_ENABLED", "1"):
        return True, "predeploy_s3_probe_skipped enabled=0"

    client_name = str(
        getenv("PREDEPLOY_S3_PROBE_CLIENT_NAME", _DEFAULT_PROBE_CLIENT_NAME) or ""
    ).strip()
    if not client_name:
        return False, "predeploy_s3_probe_failed reason=empty_client_name"

    bucket = str(
        getenv(
            "PREDEPLOY_S3_PROBE_BUCKET",
            getenv("S3_CLIENT_FORM_BUCKET", _DEFAULT_PROBE_BUCKET),
        )
        or ""
    ).strip() or _DEFAULT_PROBE_BUCKET
    prefix = str(
        getenv(
            "PREDEPLOY_S3_PROBE_PREFIX",
            getenv("S3_CLIENT_FORM_PREFIX", _DEFAULT_PROBE_PREFIX),
        )
        or ""
    ).strip() or _DEFAULT_PROBE_PREFIX
    scan_limit = _env_int(
        getenv,
        "PREDEPLOY_S3_PROBE_SCAN_LIMIT",
        _DEFAULT_PROBE_SCAN_LIMIT,
    )
    expected_key = _normalize_expected_key(
        str(
            getenv(
                "PREDEPLOY_S3_PROBE_EXPECTED_KEY",
                _DEFAULT_PROBE_EXPECTED_KEY,
            )
            or ""
        ).strip()
    )

    try:
        match = finder(
            client_name=client_name,
            bucket=bucket,
            prefix=prefix,
            max_scan=scan_limit,
        )
    except Exception as exc:
        return (
            False,
            "predeploy_s3_probe_failed "
            f"reason=lookup_exception client_name={client_name!r} "
            f"bucket={bucket!r} prefix={prefix!r} err={exc}",
        )

    if not (isinstance(match, tuple) and len(match) == 2):
        return (
            False,
            "predeploy_s3_probe_failed "
            f"reason=no_match client_name={client_name!r} bucket={bucket!r} prefix={prefix!r}",
        )

    key, payload = match
    key_str = str(key or "").strip()
    if not key_str:
        return (
            False,
            "predeploy_s3_probe_failed "
            f"reason=empty_key client_name={client_name!r}",
        )
    if expected_key and key_str != expected_key:
        return (
            False,
            "predeploy_s3_probe_failed "
            f"reason=unexpected_key expected={expected_key!r} actual={key_str!r}",
        )
    if not isinstance(payload, dict):
        return (
            False,
            "predeploy_s3_probe_failed "
            f"reason=payload_not_object key={key_str!r}",
        )

    return (
        True,
        "predeploy_s3_probe_ok "
        f"client_name={client_name!r} bucket={bucket!r} key={key_str!r}",
    )
