from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional, List, Dict

import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", "86400"))
COMPLETED_RETENTION_SECONDS = int(os.getenv("COMPLETED_RETENTION_SECONDS", "43200"))
MONTHLY_LOG_KEEP_SECONDS = int(os.getenv("MONTHLY_LOG_KEEP_SECONDS", "31536000"))
ARCHIVE_LOG_LINES = int(os.getenv("ARCHIVE_LOG_LINES", "200"))


def _client() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def _k(job_id: str, field: str) -> str:
    return f"job:{job_id}:{field}"


def _month_key(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


async def register_job(job_id: str) -> None:
    r = _client()
    ts = int(time.time())
    await r.zadd("jobs:index", {job_id: ts})
    await r.expire("jobs:index", JOB_TTL_SECONDS)


async def list_jobs(limit: int = 100, newest_first: bool = True) -> List[str]:
    await purge_inactive()
    r = _client()
    if newest_first:
        return await r.zrevrange("jobs:index", 0, limit - 1)
    return await r.zrange("jobs:index", 0, limit - 1)


async def set_status(job_id: str, status: str) -> None:
    r = _client()
    await r.set(_k(job_id, "status"), status, ex=JOB_TTL_SECONDS)
    s = (status or "").lower()
    if s in ("completed", "failed"):
        ts = int(time.time())
        await r.zadd("jobs:inactive", {job_id: ts})
        await r.expire("jobs:inactive", JOB_TTL_SECONDS)
        await archive_job_snapshot(job_id, s, ts)
        await r.expire(_k(job_id, "status"), COMPLETED_RETENTION_SECONDS)
        await r.expire(_k(job_id, "result"), COMPLETED_RETENTION_SECONDS)
        await r.expire(_k(job_id, "progress"), COMPLETED_RETENTION_SECONDS)
        await r.expire(_k(job_id, "log"), COMPLETED_RETENTION_SECONDS)
        await r.expire(_k(job_id, "ctr:pages_total"), COMPLETED_RETENTION_SECONDS)
        await r.expire(_k(job_id, "ctr:pages_done"), COMPLETED_RETENTION_SECONDS)
        await r.expire(_k(job_id, "ctr:pages_failed"), COMPLETED_RETENTION_SECONDS)
        await r.expire(_k(job_id, "ctr:pages_skipped"), COMPLETED_RETENTION_SECONDS)


async def get_status(job_id: str) -> Optional[str]:
    r = _client()
    return await r.get(_k(job_id, "status"))


async def set_result(job_id: str, result: Any) -> None:
    r = _client()
    await r.set(_k(job_id, "result"), json.dumps(result, ensure_ascii=False, separators=(",", ":")), ex=JOB_TTL_SECONDS)


async def get_result(job_id: str) -> Any:
    r = _client()
    raw = await r.get(_k(job_id, "result"))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


async def set_progress(job_id: str, data: Dict[str, Any]) -> None:
    r = _client()
    await r.set(_k(job_id, "progress"), json.dumps(data, ensure_ascii=False, separators=(",", ":")), ex=JOB_TTL_SECONDS)


async def get_progress(job_id: str) -> Dict[str, Any]:
    r = _client()
    raw = await r.get(_k(job_id, "progress"))
    if raw is None:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


async def append_log(job_id: str, line: str) -> None:
    r = _client()
    key = _k(job_id, "log")
    await r.rpush(key, line)
    await r.expire(key, JOB_TTL_SECONDS)


async def get_log(job_id: str, limit: int = 200) -> List[str]:
    r = _client()
    items = await r.lrange(_k(job_id, "log"), -limit, -1)
    return items or []


async def incr_counter(job_id: str, name: str, amount: int = 1) -> int:
    r = _client()
    key = _k(job_id, f"ctr:{name}")
    val = await r.incrby(key, amount)
    await r.expire(key, JOB_TTL_SECONDS)
    return int(val)


async def get_counter(job_id: str, name: str) -> int:
    r = _client()
    raw = await r.get(_k(job_id, f"ctr:{name}"))
    try:
        return int(raw) if raw is not None else 0
    except Exception:
        return 0


async def purge_inactive() -> int:
    r = _client()
    now = int(time.time())
    cutoff = now - COMPLETED_RETENTION_SECONDS
    old_ids = await r.zrangebyscore("jobs:inactive", 0, cutoff)
    if not old_ids:
        return 0
    await r.zrem("jobs:inactive", *old_ids)
    await r.zrem("jobs:index", *old_ids)
    keys = []
    for jid in old_ids:
        keys.extend(
            [
                _k(jid, "status"),
                _k(jid, "result"),
                _k(jid, "progress"),
                _k(jid, "log"),
                _k(jid, "ctr:pages_total"),
                _k(jid, "ctr:pages_done"),
                _k(jid, "ctr:pages_failed"),
                _k(jid, "ctr:pages_skipped"),
            ]
        )
    if keys:
        await r.delete(*keys)
    return len(old_ids)


async def archive_job_snapshot(job_id: str, status: str, finished_ts: int) -> None:
    r = _client()
    prog = await get_progress(job_id)
    logs = await get_log(job_id, ARCHIVE_LOG_LINES)
    pages_total = await get_counter(job_id, "pages_total")
    pages_done = await get_counter(job_id, "pages_done")
    pages_failed = await get_counter(job_id, "pages_failed")
    pages_skipped = await get_counter(job_id, "pages_skipped")

    month = _month_key(finished_ts)
    key = f"queue_logs:{month}"

    payload = {
        "job_id": job_id,
        "status": status,
        "finished_at_utc": datetime.fromtimestamp(finished_ts, tz=timezone.utc).isoformat(),
        "progress": prog,
        "counters": {
            "pages_total": pages_total,
            "pages_done": pages_done,
            "pages_failed": pages_failed,
            "pages_skipped": pages_skipped,
        },
        "log_tail": logs,
    }

    await r.rpush(key, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    await r.expire(key, MONTHLY_LOG_KEEP_SECONDS)


async def get_monthly_queue_logs(month_yyyy_mm: str) -> List[Dict[str, Any]]:
    r = _client()
    raw_items = await r.lrange(f"queue_logs:{month_yyyy_mm}", 0, -1)
    out: List[Dict[str, Any]] = []
    for raw in raw_items or []:
        try:
            val = json.loads(raw)
            if isinstance(val, dict):
                out.append(val)
        except Exception:
            continue
    return out


async def clear_monthly_queue_logs(month_yyyy_mm: str) -> None:
    r = _client()
    await r.delete(f"queue_logs:{month_yyyy_mm}")