from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from .s3_upload import download_json, upload_delivered_copy

logger = logging.getLogger(__name__)


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _payload_base_dir() -> str:
    # On Render, mount your Persistent Disk at something like /var/data
    # and set PAYLOAD_DISK_DIR to a subdirectory within it (e.g. /var/data/procontentapi).
    return _clean_str(os.getenv("PAYLOAD_DISK_DIR", "")) or "/var/data/procontentapi"


def payload_dir() -> str:
    base = _payload_base_dir()
    path = os.path.join(base, "payloads")
    os.makedirs(path, exist_ok=True)
    return path


def payload_path_for_job(job_id: str) -> str:
    job_id = _clean_str(job_id)
    if not job_id:
        raise ValueError("job_id missing for payload path")
    return os.path.join(payload_dir(), f"{job_id}.json")


def save_payload_json(job_id: str, data: Any) -> str:
    path = payload_path_for_job(job_id)
    tmp = f"{path}.tmp"
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, path)
    try:
        size = os.path.getsize(path)
    except Exception:
        size = -1
    logger.info("payload_saved job_id=%s path=%s bytes=%s", job_id, path, size)
    return path


def load_payload_json(ref: str) -> Any | None:
    ref = _clean_str(ref)
    if not ref:
        return None

    # Local file path refs.
    path = ref
    if ref.startswith("file:"):
        path = ref[len("file:") :].strip()
    if path.startswith("/") or path.startswith("./"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("payload_read_failed path=%s err=%s", path, exc)
            return None

    # Fallback: treat as S3 key (legacy / archived).
    return download_json(ref)


def maybe_archive_payload_to_s3(job_id: str, client_name: str, data: Any) -> str | None:
    enabled = _clean_str(os.getenv("ARCHIVE_TO_S3_ON_SEND", "0"))
    if enabled not in ("1", "true", "yes", "on"):
        return None
    try:
        key = upload_delivered_copy(job_id=job_id, client_name=client_name, data=data)
        logger.info("payload_archived_s3_ok job_id=%s key=%s", job_id, key)
        return key
    except Exception as exc:
        # Delivery already succeeded; don't fail delivery if archival fails.
        logger.warning("payload_archived_s3_failed job_id=%s err=%s", job_id, exc)
        return None


def purge_payload_file(job_id: str) -> bool:
    path = payload_path_for_job(job_id)
    try:
        os.remove(path)
        logger.info("payload_purged job_id=%s path=%s", job_id, path)
        return True
    except FileNotFoundError:
        logger.info("payload_purged_missing job_id=%s path=%s", job_id, path)
        return True
    except Exception as exc:
        logger.warning("payload_purge_failed job_id=%s path=%s err=%s", job_id, path, exc)
        return False


def retention_seconds() -> int:
    days_raw = _clean_str(os.getenv("PAYLOAD_RETENTION_DAYS", "7"))
    try:
        days = int(days_raw)
    except Exception:
        days = 7
    if days < 0:
        days = 0
    return days * 24 * 60 * 60


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

