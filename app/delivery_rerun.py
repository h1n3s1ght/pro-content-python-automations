from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict

from .job_input_store import get_job_input_payload, upsert_job_input
from .storage import register_job, set_payload, set_status
from .tasks import run_full_job


def queue_rerun_from_job_id(job_id: str) -> str:
    source_job_id = str(job_id or "").strip()
    payload = get_job_input_payload(source_job_id)
    if not isinstance(payload, dict):
        raise LookupError(f"missing rerun source payload for job_id={source_job_id}")
    return queue_rerun_from_payload(payload)


def queue_rerun_from_payload(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise ValueError("rerun payload must be an object")

    new_job_id = str(uuid.uuid4())
    asyncio.run(register_job(new_job_id))
    asyncio.run(set_status(new_job_id, "queued"))
    asyncio.run(set_payload(new_job_id, payload))
    upsert_job_input(job_id=new_job_id, input_payload=payload)
    run_full_job.delay(new_job_id, payload)
    return new_job_id

