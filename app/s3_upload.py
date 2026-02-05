from __future__ import annotations

import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional

import boto3
import logging


AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
S3_BUCKET = os.getenv("S3_BUCKET", "pro-tier-bucket")
S3_SITEMAPS_PREFIX = os.getenv("S3_SITEMAPS_PREFIX", "sitemaps/")
S3_FULLCONTENT_PREFIX = os.getenv("S3_FULLCONTENT_PREFIX", "fullContent/")
S3_MONTHLY_LOGS_PREFIX = os.getenv("S3_MONTHLY_LOGS_PREFIX", "monthly-queue-logs/")


_s3 = boto3.client("s3", region_name=AWS_REGION)
_CST = ZoneInfo("America/Chicago")
logger = logging.getLogger(__name__)




def _safe_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^A-Za-z0-9_\-]+", "", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_") or "business"


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


def upload_monthly_logs(month_name: str, year: int, logs: Any) -> str:
    filename = build_monthly_logs_filename(month_name, year)
    return upload_json(S3_MONTHLY_LOGS_PREFIX, filename, logs)


def download_json(key: str) -> Any:
    if not key:
        return None
    try:
        resp = _s3.get_object(Bucket=S3_BUCKET, Key=key)
        body = resp["Body"].read()
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        return json.loads(body)
    except Exception:
        return None


def head_object_info(key: str) -> Dict[str, Any] | None:
    if not key:
        return None
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
