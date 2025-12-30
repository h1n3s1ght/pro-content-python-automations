from __future__ import annotations

import os
import uuid

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException

from .models import WebhookInput
from .tasks import run_full_job
from .storage import get_result, get_status, register_job, set_status
from .ui import router as ui_router

load_dotenv()

API_BEARER_TOKEN = os.getenv("API_BEARER_TOKEN", "").strip()

app = FastAPI()
app.include_router(ui_router)


async def require_bearer(authorization: str | None = Header(default=None)) -> None:
    if not API_BEARER_TOKEN:
        raise HTTPException(status_code=500, detail="Server missing API_BEARER_TOKEN")

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization[len(prefix) :].strip()
    if token != API_BEARER_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/webhook/pro-form", dependencies=[Depends(require_bearer)])
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
