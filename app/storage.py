from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional, List, Dict, Tuple

import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", "86400"))
COMPLETED_RETENTION_SECONDS = int(os.getenv("COMPLETED_RETENTION_SECONDS", "43200"))
MONTHLY_LOG_KEEP_SECONDS = int(os.getenv("MONTHLY_LOG_KEEP_SECONDS", "31536000"))
ARCHIVE_LOG_LINES = int(os.getenv("ARCHIVE_LOG_LINES", "200"))
PAYLOAD_TTL_SECONDS = JOB_TTL_SECONDS


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


async def list_jobs_with_scores(
    limit: int = 100,
    newest_first: bool = True,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
) -> List[Tuple[str, float]]:
    await purge_inactive()
    r = _client()
    min_s = min_score if min_score is not None else "-inf"
    max_s = max_score if max_score is not None else "+inf"

    if min_score is not None or max_score is not None:
        if newest_first:
            items = await r.zrevrangebyscore("jobs:index", max_s, min_s, start=0, num=limit, withscores=True)
        else:
            items = await r.zrangebyscore("jobs:index", min_s, max_s, start=0, num=limit, withscores=True)
    else:
        if newest_first:
            items = await r.zrevrange("jobs:index", 0, limit - 1, withscores=True)
        else:
            items = await r.zrange("jobs:index", 0, limit - 1, withscores=True)

    out: List[Tuple[str, float]] = []
    for jid, sc in items or []:
        try:
            out.append((jid, float(sc)))
        except Exception:
            continue
    return out


async def set_status(job_id: str, status: str) -> None:
    r = _client()
    await r.set(_k(job_id, "status"), status, ex=JOB_TTL_SECONDS)
    s = (status or "").lower()
    if s in ("completed", "failed", "canceled"):
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
        await r.expire(_k(job_id, "payload"), COMPLETED_RETENTION_SECONDS)
        await r.expire(_k(job_id, "resume_mode"), COMPLETED_RETENTION_SECONDS)


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


async def set_payload(job_id: str, payload: Any) -> None:
    r = _client()
    await r.set(
        _k(job_id, "payload"),
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
        ex=PAYLOAD_TTL_SECONDS,
    )


async def get_payload(job_id: str) -> Any:
    r = _client()
    raw = await r.get(_k(job_id, "payload"))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


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
    if not isinstance(line, str):
        line = str(line)
    # Ensure level prefix exists: [I]/[D]/[W]/[E]/[*]
    if not (len(line) >= 3 and line.startswith("[") and line[2] == "]"):
        line = f"[I] {line}"
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
                _k(jid, "paused"),
                _k(jid, "payload"),
                _k(jid, "canceled"),
                _k(jid, "resume_mode"),
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


async def is_paused(job_id: str) -> bool:
    r = _client()
    v = await r.get(_k(job_id, "paused"))
    return str(v or "").strip() == "1"


async def request_cancel(job_id: str) -> None:
    r = _client()
    await r.set(_k(job_id, "canceled"), "1", ex=JOB_TTL_SECONDS)
    await r.expire(_k(job_id, "canceled"), JOB_TTL_SECONDS)


async def is_canceled(job_id: str) -> bool:
    r = _client()
    v = await r.get(_k(job_id, "canceled"))
    return str(v or "").strip() == "1"


async def set_resume_mode(job_id: str) -> None:
    r = _client()
    await r.set(_k(job_id, "resume_mode"), "1", ex=JOB_TTL_SECONDS)


async def is_resume_mode(job_id: str) -> bool:
    r = _client()
    v = await r.get(_k(job_id, "resume_mode"))
    return str(v or "").strip() == "1"


async def pause_job(job_id: str) -> bool:
    s = (await get_status(job_id) or "").lower()
    if s not in ("queued", "paused", "running", "starting"):
        return False
    r = _client()
    await r.set(_k(job_id, "paused"), "1", ex=JOB_TTL_SECONDS)
    if s != "paused":
        await set_status(job_id, "paused")
        await append_log(job_id, "job_paused_by_user")
    return True


async def resume_job(job_id: str) -> bool:
    s = (await get_status(job_id) or "").lower()
    if s != "paused":
        return False
    r = _client()
    await r.delete(_k(job_id, "paused"))
    await set_status(job_id, "queued")
    ts = int(time.time())
    await r.zadd("jobs:index", {job_id: ts})
    await r.expire("jobs:index", JOB_TTL_SECONDS)
    await set_resume_mode(job_id)
    await append_log(job_id, "job_resumed_by_user")
    return True


async def cancel_queued_job(job_id: str) -> bool:
    s = (await get_status(job_id) or "").lower()
    if s not in ("queued", "paused", "running", "starting"):
        return False
    await request_cancel(job_id)
    await append_log(job_id, "job_canceled_by_user")
    await set_status(job_id, "canceled")
    await set_progress(job_id, {"stage": "canceled"})
    return True


async def move_job(job_id: str, direction: str) -> bool:
    s = (await get_status(job_id) or "").lower()
    if s not in ("queued", "paused"):
        return False

    r = _client()
    direction = (direction or "").lower().strip()

    items: List[Tuple[str, float]] = await r.zrange("jobs:index", 0, -1, withscores=True)
    if not items:
        return False

    ids = [jid for jid, _ in items]
    if job_id not in ids:
        return False

    scores = [sc for _, sc in items]
    min_score = min(scores)
    max_score = max(scores)

    cur_score = None
    for jid, sc in items:
        if jid == job_id:
            cur_score = float(sc)
            break
    if cur_score is None:
        return False

    if direction == "top":
        new_score = min_score - 1.0
    elif direction == "bottom":
        new_score = max_score + 1.0
    elif direction in ("up", "down"):
        idx = ids.index(job_id)
        if direction == "up":
            if idx == 0:
                return True
            neighbor_id = ids[idx - 1]
        else:
            if idx == len(ids) - 1:
                return True
            neighbor_id = ids[idx + 1]

        neighbor_score = None
        for jid, sc in items:
            if jid == neighbor_id:
                neighbor_score = float(sc)
                break
        if neighbor_score is None:
            return False

        await r.zadd("jobs:index", {job_id: neighbor_score, neighbor_id: cur_score})
        await r.expire("jobs:index", JOB_TTL_SECONDS)
        return True
    else:
        return False

    await r.zadd("jobs:index", {job_id: new_score})
    await r.expire("jobs:index", JOB_TTL_SECONDS)
    return True
