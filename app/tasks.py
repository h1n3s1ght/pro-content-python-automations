import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from celery.exceptions import SoftTimeLimitExceeded

from .celery_app import celery_app
from .workflow import run_workflow
from .storage import set_status, set_result, set_progress, append_log, is_paused, get_progress
from .monthly_logs import upload_monthly_queue_logs


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    soft_time_limit=60 * 29,
    time_limit=60 * 30,
)
def run_full_job(self, job_id: str, payload: dict):
    try:
        if asyncio.run(is_paused(job_id)):
            asyncio.run(_paused_wait(job_id))
            raise self.retry(countdown=30)
        asyncio.run(_run(job_id, payload))
    except SoftTimeLimitExceeded:
        asyncio.run(_timeout(job_id))
        raise self.retry(countdown=60)


async def _paused_wait(job_id: str):
    await set_status(job_id, "paused")
    await append_log(job_id, "job_paused_waiting")


async def _timeout(job_id: str):
    await set_status(job_id, "queued")
    await set_progress(job_id, {"stage": "queued", "reason": "timeout_requeued"})
    await append_log(job_id, "job_timeout_requeued")


async def _run(job_id: str, payload: dict):
    await set_status(job_id, "running")
    await set_progress(job_id, {"stage": "starting"})
    try:
        await append_log(job_id, "job_started")
        result = await run_workflow(payload, job_id=job_id)
        await set_result(job_id, result)
        await set_status(job_id, "completed")
        cur = await get_progress(job_id) or {}
        cur["stage"] = "completed"
        await set_progress(job_id, cur)
        await append_log(job_id, "job_completed")
    except SoftTimeLimitExceeded:
        await _timeout(job_id)
        raise
    except Exception as e:
        await set_status(job_id, "failed")
        await set_result(job_id, {"error": str(e)})
        await set_progress(job_id, {"stage": "failed"})
        await append_log(job_id, f"job_failed: {str(e)}")
        raise


@celery_app.task(
    name="app.tasks.upload_previous_month_queue_logs",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def upload_previous_month_queue_logs():
    tz = ZoneInfo("America/Chicago")
    now = datetime.now(tz=tz)

    year = now.year
    month = now.month - 1
    if month == 0:
        month = 12
        year -= 1

    month_yyyy_mm = f"{year:04d}-{month:02d}"
    asyncio.run(upload_monthly_queue_logs(month_yyyy_mm))
