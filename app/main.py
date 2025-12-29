from __future__ import annotations

import uuid

from dotenv import load_dotenv
from fastapi import FastAPI

from .models import WebhookInput
from .tasks import run_full_job
from .storage import get_result, get_status, register_job, set_status
from .ui import router as ui_router

load_dotenv()

app = FastAPI()
app.include_router(ui_router)


@app.post("/webhook/pro-form")
async def webhook_pro_form(payload: WebhookInput):
    job_id = str(uuid.uuid4())
    await register_job(job_id)
    await set_status(job_id, "queued")
    run_full_job.delay(job_id, payload.model_dump())
    return {"job_id": job_id, "status": "queued"}


@app.get("/result/{job_id}")
async def get_result_endpoint(job_id: str):
    return {
        "job_id": job_id,
        "status": await get_status(job_id),
        "result": await get_result(job_id),
    }

@app.get("/healthz")
async def healthz():
    return {"ok": True}
