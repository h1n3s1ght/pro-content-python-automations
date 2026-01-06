from __future__ import annotations

from typing import Any, Dict, Optional

from .errors import OperationCanceled, PauseRequested
from .storage import append_log, get_progress, is_canceled, is_paused
from .workflow import run_workflow


async def _ensure_can_continue(job_id: Optional[str]) -> None:
    if not job_id:
        return
    if await is_canceled(job_id):
        raise OperationCanceled("job canceled by user")
    if await is_paused(job_id):
        raise PauseRequested("job paused by user")


async def run_resume_workflow(webhook_payload: Dict[str, Any], job_id: Optional[str] = None) -> Dict[str, Any]:
    await _ensure_can_continue(job_id)

    if job_id:
        await append_log(job_id, "resume_workflow_start")

    progress = await get_progress(job_id) if job_id else {}
    sitemap_ok = bool((webhook_payload or {}).get("sitemap_data"))
    if job_id:
        await append_log(job_id, f"resume_validation_sitemap_present={sitemap_ok}")
        await append_log(job_id, f"resume_previous_progress_stage={progress.get('stage', '')}")

    await _ensure_can_continue(job_id)

    if job_id:
        await append_log(job_id, "resume_strategy:rerun_full_workflow_for_safety")

    return await run_workflow(webhook_payload, job_id=job_id)
