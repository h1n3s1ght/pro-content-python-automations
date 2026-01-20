from __future__ import annotations

import logging
import os
import uuid

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request

from .models import WebhookInput
from .tasks import run_full_job
from .storage import get_result, get_status, register_job, set_status, set_payload
from .ui import router as ui_router
from .deliveries import router as deliveries_router
from .admin import router as admin_router
from .webhook_utils import collect_unknown_fields, normalize_webhook_payload

load_dotenv()

API_BEARER_TOKEN = os.getenv("API_BEARER_TOKEN", "").strip()

app = FastAPI()
app.include_router(ui_router)
app.include_router(deliveries_router)
app.include_router(admin_router)
logger = logging.getLogger(__name__)


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
async def webhook_pro_form(payload: WebhookInput, request: Request):
    job_id = str(uuid.uuid4())
    raw_body = None
    try:
        raw_body = await request.json()
    except Exception:
        raw_body = None
    if isinstance(raw_body, dict):
        normalized = normalize_webhook_payload(raw_body)
        top_keys = sorted(normalized.keys())
        unknown_keys = sorted(set(collect_unknown_fields(normalized, WebhookInput)))
        if top_keys:
            logger.info("webhook_pro_form keys job_id=%s keys=%s", job_id, top_keys)
        if unknown_keys:
            logger.warning("webhook_pro_form unknown_keys job_id=%s keys=%s", job_id, unknown_keys)
    await register_job(job_id)
    await set_status(job_id, "queued")
    payload_dict = payload.model_dump(by_alias=False, mode="json")
    await set_payload(job_id, payload_dict)
    run_full_job.delay(job_id, payload_dict)
    return {"job_id": job_id, "status": "queued"}


@app.get("/result/{job_id}")
async def get_result_endpoint(job_id: str):
    return {
        "job_id": job_id,
        "status": await get_status(job_id),
        "result": await get_result(job_id),
    }
