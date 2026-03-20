from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional

import boto3
import logging


AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
S3_BUCKET = os.getenv("S3_BUCKET", "pro-tier-bucket")
S3_SITEMAPS_PREFIX = os.getenv("S3_SITEMAPS_PREFIX", "sitemaps/")
S3_FULLCONTENT_PREFIX = os.getenv("S3_FULLCONTENT_PREFIX", "fullContent/")
S3_MONTHLY_LOGS_PREFIX = os.getenv("S3_MONTHLY_LOGS_PREFIX", "monthly-queue-logs/")
S3_CLIENT_FORM_BUCKET = os.getenv("S3_CLIENT_FORM_BUCKET", "pro-tier-bucket")
S3_CLIENT_FORM_PREFIX = os.getenv("S3_CLIENT_FORM_PREFIX", "clientForm/")
S3_CLIENT_FORM_SCAN_LIMIT = int(os.getenv("S3_CLIENT_FORM_SCAN_LIMIT", "2000"))


_s3 = boto3.client("s3", region_name=AWS_REGION)
_CST = ZoneInfo("America/Chicago")
logger = logging.getLogger(__name__)

_CLIENT_FORM_TS_RE = re.compile(
    r"^(?P<name>.+?)_(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:-\d+)?Z)$"
)




def _safe_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^A-Za-z0-9_\-]+", "", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_") or "business"


def _client_form_prefix(prefix: str) -> str:
    raw = (prefix or "").strip()
    if not raw:
        raw = "clientForm/"
    return raw if raw.endswith("/") else f"{raw}/"


def _safe_client_form_name(client_name: str) -> str:
    raw = str(client_name or "").strip()
    if not raw:
        return ""
    # Remove punctuation while preserving spaces, then normalize underscores.
    raw = re.sub(r"[^A-Za-z0-9\s]+", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""
    parts = []
    for token in raw.split(" "):
        t = token.strip()
        if not t:
            continue
        if t.isupper() and len(t) <= 4:
            parts.append(t)
        else:
            parts.append(t[:1].upper() + t[1:].lower())
    return "_".join(parts)


def _name_signature(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _basename_without_ext(key: str) -> str:
    name = str(key or "").rsplit("/", 1)[-1]
    if name.lower().endswith(".json"):
        return name[:-5]
    return name


def _split_client_form_basename(base: str) -> tuple[str, str] | None:
    match = _CLIENT_FORM_TS_RE.match(base)
    if not match:
        return None
    return match.group("name"), match.group("ts")


def _parse_client_form_ts(ts: str) -> datetime:
    # Examples: 2026-02-20T06-19-06-992Z or 2026-02-20T06-19-06Z
    raw = str(ts or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    if raw.count("-") >= 6:
        # milliseconds section exists
        raw = raw.replace("T", " ", 1)
        main, ms = raw.rsplit("-", 1)
        return datetime.strptime(f"{main}.{ms}", "%Y-%m-%d %H-%M-%S.%f").replace(tzinfo=timezone.utc)
    return datetime.strptime(raw.replace("T", " ", 1), "%Y-%m-%d %H-%M-%S").replace(tzinfo=timezone.utc)


def _list_keys_for_prefix(*, bucket: str, prefix: str, limit: int) -> tuple[list[dict], str]:
    paginator = _s3.get_paginator("list_objects_v2")
    out: list[dict] = []
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            contents = page.get("Contents") or []
            for item in contents:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("Key") or "")
                if not key:
                    continue
                out.append(item)
                if len(out) >= limit:
                    return out, ""
    except Exception as exc:
        logger.warning(
            "s3_client_form_list_failed bucket=%s prefix=%s err=%s",
            bucket,
            prefix,
            exc,
        )
        return [], str(exc)
    return out, ""


def _pick_latest_client_form_object(items: list[dict], *, expected_signature: str = "") -> dict | None:
    candidates: list[tuple[datetime, datetime, str, dict]] = []
    for item in items:
        key = str(item.get("Key") or "")
        if not key.lower().endswith(".json"):
            continue
        base = _basename_without_ext(key)
        split = _split_client_form_basename(base)
        if split is None:
            continue
        name_part, ts_part = split
        if expected_signature and _name_signature(name_part) != expected_signature:
            continue
        try:
            parsed_ts = _parse_client_form_ts(ts_part)
        except Exception:
            parsed_ts = datetime.min.replace(tzinfo=timezone.utc)
        last_modified = item.get("LastModified")
        if isinstance(last_modified, datetime):
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)
            else:
                last_modified = last_modified.astimezone(timezone.utc)
        else:
            last_modified = datetime.min.replace(tzinfo=timezone.utc)
        candidates.append((last_modified, parsed_ts, key, item))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return candidates[0][3]


def _unpack_list_result(value: Any) -> tuple[list[dict], str]:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], list):
        return value[0], str(value[1] or "")
    if isinstance(value, list):
        return value, ""
    return [], ""


def _compact_error(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)


def _find_latest_client_form_payload_internal(
    *,
    client_name: str,
    bucket: str | None = None,
    prefix: str | None = None,
    max_scan: int | None = None,
) -> tuple[tuple[str, Dict[str, Any]] | None, str]:
    name = str(client_name or "").strip()
    if not name:
        return None, "missing_client_name"

    bucket_name = str(bucket or S3_CLIENT_FORM_BUCKET).strip() or S3_CLIENT_FORM_BUCKET
    prefix_root = _client_form_prefix(str(prefix or S3_CLIENT_FORM_PREFIX))
    safe_name = _safe_client_form_name(name)
    signature = _name_signature(name)
    scan_limit = int(max_scan or S3_CLIENT_FORM_SCAN_LIMIT)
    if scan_limit < 1:
        scan_limit = 1

    # Fast-path: exact prefix in expected filename format.
    exact_prefix = f"{prefix_root}{safe_name}_"
    fast_items_raw = _list_keys_for_prefix(bucket=bucket_name, prefix=exact_prefix, limit=scan_limit)
    fast_items, fast_err = _unpack_list_result(fast_items_raw)
    latest = _pick_latest_client_form_object(fast_items)

    # Fallback: broader scan with name-signature matching.
    broad_items: list[dict] = []
    broad_err = ""
    if latest is None:
        broad_items_raw = _list_keys_for_prefix(bucket=bucket_name, prefix=prefix_root, limit=scan_limit)
        broad_items, broad_err = _unpack_list_result(broad_items_raw)
        latest = _pick_latest_client_form_object(broad_items, expected_signature=signature)

    if latest is None:
        diagnostics = (
            f"client_name={name!r} safe_name={safe_name!r} bucket={bucket_name!r} "
            f"exact_prefix={exact_prefix!r} exact_items={len(fast_items)} exact_err={_compact_error(fast_err)!r} "
            f"broad_prefix={prefix_root!r} broad_items={len(broad_items)} broad_err={_compact_error(broad_err)!r}"
        )
        logger.warning(
            "s3_client_form_no_match %s",
            diagnostics,
        )
        return None, diagnostics

    key = str(latest.get("Key") or "").strip()
    if not key:
        return None, "empty_s3_key"

    payload = download_json(key, bucket=bucket_name)
    if not isinstance(payload, dict):
        diagnostics = f"client_name={name!r} bucket={bucket_name!r} key={key!r}"
        logger.warning(
            "s3_client_form_payload_download_failed %s",
            diagnostics,
        )
        return None, diagnostics
    logger.info(
        "s3_client_form_match client_name=%s bucket=%s key=%s",
        name,
        bucket_name,
        key,
    )
    return (key, payload), f"client_name={name!r} bucket={bucket_name!r} key={key!r}"


def datetime_cst_stamp(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = datetime.now(tz=_CST)
    else:
        dt = dt.astimezone(_CST)
    return dt.strftime("%Y-%m-%d_%H-%M-%S")


def build_filename(business_name: str, stamp: str, kind: str) -> str:
    bn = _safe_name(business_name)
    return f"{bn}_{stamp}_{kind}.json"

def build_monthly_logs_filename(month_name: str, year: int) -> str:
    safe_month = _safe_name(month_name)
    return f"{safe_month}_{year}_QueueLogs.json"


def upload_json(prefix: str, filename: str, data: Any) -> str:
    if not prefix.endswith("/"):
        prefix += "/"
    key = f"{prefix}{filename}"

    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    try:
        logger.info(
            "s3_put_object_start bucket=%s key=%s bytes=%s region=%s",
            S3_BUCKET,
            key,
            len(body),
            AWS_REGION,
        )
        _s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=body,
            ContentType="application/json; charset=utf-8",
        )
        logger.info("s3_put_object_ok bucket=%s key=%s", S3_BUCKET, key)
    except Exception as exc:
        logger.exception(
            "s3_put_object_failed bucket=%s key=%s region=%s err=%s",
            S3_BUCKET,
            key,
            AWS_REGION,
            exc,
        )
        raise
    return key


def upload_sitemap(metadata: Dict[str, Any], sitemap_data: Dict[str, Any], stamp: str) -> str:
    business_name = (
    (metadata or {}).get("businessName")
    or (metadata or {}).get("business_name")
    or (metadata or {}).get("business_name_sanitized")
    or "business"
)
    filename = build_filename(business_name, stamp, "sitemap")
    return upload_json(S3_SITEMAPS_PREFIX, filename, sitemap_data)


def upload_copy(metadata: Dict[str, Any], final_copy: Dict[str, Any], stamp: str) -> str:
    business_name = (
    (metadata or {}).get("businessName")
    or (metadata or {}).get("business_name")
    or (metadata or {}).get("business_name_sanitized")
    or "business"
)
    filename = build_filename(business_name, stamp, "copy")
    return upload_json(S3_FULLCONTENT_PREFIX, filename, final_copy)


def upload_delivered_copy(*, job_id: str, client_name: str, data: Any) -> str:
    """
    Best-effort archival copy after a successful delivery send.
    Stored separately from "fullContent/" generation outputs.
    """
    prefix = os.getenv("S3_DELIVERED_PREFIX", "delivered/")
    safe_client = _safe_name(client_name or "client")
    safe_job = _safe_name(job_id or "job")
    filename = f"{safe_client}_{safe_job}.json"
    return upload_json(prefix, filename, data)


def upload_monthly_logs(month_name: str, year: int, logs: Any) -> str:
    filename = build_monthly_logs_filename(month_name, year)
    return upload_json(S3_MONTHLY_LOGS_PREFIX, filename, logs)


def download_json(key: str, *, bucket: str | None = None) -> Any:
    if not key:
        return None
    bucket_name = str(bucket or S3_BUCKET).strip() or S3_BUCKET
    try:
        resp = _s3.get_object(Bucket=bucket_name, Key=key)
        body = resp["Body"].read()
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        return json.loads(body)
    except Exception:
        return None


def find_latest_client_form_payload(
    *,
    client_name: str,
    bucket: str | None = None,
    prefix: str | None = None,
    max_scan: int | None = None,
) -> tuple[str, Dict[str, Any]] | None:
    """
    Find newest client form payload from S3 by client-name prefix and return:
      (key, payload_dict)
    """
    try:
        match, _diagnostics = _find_latest_client_form_payload_internal(
            client_name=client_name,
            bucket=bucket,
            prefix=prefix,
            max_scan=max_scan,
        )
        return match
    except Exception as exc:
        logger.warning(
            "s3_client_form_lookup_failed client_name=%s bucket=%s prefix=%s err=%s",
            str(client_name or "").strip(),
            str(bucket or S3_CLIENT_FORM_BUCKET).strip() or S3_CLIENT_FORM_BUCKET,
            _client_form_prefix(str(prefix or S3_CLIENT_FORM_PREFIX)),
            exc,
        )
        return None


def find_latest_client_form_payload_with_diagnostics(
    *,
    client_name: str,
    bucket: str | None = None,
    prefix: str | None = None,
    max_scan: int | None = None,
) -> tuple[tuple[str, Dict[str, Any]] | None, str]:
    try:
        return _find_latest_client_form_payload_internal(
            client_name=client_name,
            bucket=bucket,
            prefix=prefix,
            max_scan=max_scan,
        )
    except Exception as exc:
        return None, f"lookup_exception={_compact_error(str(exc))}"


def head_object_info(key: str) -> Dict[str, Any] | None:
    if not key:
        return None
    # DB ref support (when storing payloads in Postgres).
    if isinstance(key, str) and key.startswith("db:"):
        return {"type": "db", "job_id": key[len("db:") :].strip()}
    # Local payload file support (used when storing payloads on a Render Persistent Disk).
    path = key
    if isinstance(path, str) and path.startswith("file:"):
        path = path[len("file:") :].strip()
    if isinstance(path, str) and (path.startswith("/") or path.startswith("./")):
        try:
            st = os.stat(path)
            return {
                "type": "file",
                "path": path,
                "size_bytes": int(st.st_size),
                "modified_at": datetime.fromtimestamp(st.st_mtime, tz=_CST).isoformat(),
            }
        except Exception as exc:
            return {
                "type": "file",
                "path": path,
                "error": str(exc),
            }
    try:
        resp = _s3.head_object(Bucket=S3_BUCKET, Key=key)
        return {
            "bucket": S3_BUCKET,
            "key": key,
            "region": AWS_REGION,
            "content_length": int(resp.get("ContentLength") or 0),
            "content_type": resp.get("ContentType"),
            "etag": resp.get("ETag"),
            "last_modified": resp.get("LastModified").isoformat() if resp.get("LastModified") else None,
        }
    except Exception as exc:
        return {
            "bucket": S3_BUCKET,
            "key": key,
            "region": AWS_REGION,
            "error": str(exc),
        }
