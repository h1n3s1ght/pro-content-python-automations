import time
import json
import os
import logging
from uuid import UUID
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db_session
from .db_models import DeliveryOutbox
from .delivery_schemas import DeliveryListResponse, DeliveryOutboxSchema, OverrideURLRequest, SendNowResponse
from .storage import (
    list_jobs_with_scores,
    get_status,
    get_progress,
    get_log,
    cancel_queued_job,
    move_job,
    pause_job,
    resume_job,
)
from .tasks import run_resume_job, send_delivery
from .s3_upload import head_object_info

router = APIRouter(prefix="/ui", tags=["ui"])
logger = logging.getLogger(__name__)


def _safe_db_location() -> str:
    raw = (os.getenv("DATABASE_URL", "") or "").strip()
    if not raw:
        return "missing"
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    if raw.startswith("postgresql://") and "+psycopg" not in raw:
        raw = "postgresql+psycopg://" + raw[len("postgresql://") :]
    try:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
        port = parsed.port or ""
        dbname = (parsed.path or "").lstrip("/")
        if host and port and dbname:
            return f"{host}:{port}/{dbname}"
        if host and dbname:
            return f"{host}/{dbname}"
        return dbname or host or "unknown"
    except Exception:
        return "unknown"


def _badge(text: str) -> str:
    t = (text or "unknown").lower()
    if t == "completed":
        cls = "badge rounded-pill bg-success-subtle text-success-emphasis border border-success-subtle"
    elif t == "running":
        cls = "badge rounded-pill bg-primary-subtle text-primary-emphasis border border-primary-subtle"
    elif t == "failed":
        cls = "badge rounded-pill bg-danger-subtle text-danger-emphasis border border-danger-subtle"
    elif t == "queued":
        cls = "badge rounded-pill bg-warning-subtle text-warning-emphasis border border-warning-subtle"
    elif t == "paused":
        cls = "badge rounded-pill bg-orange-subtle text-orange-emphasis border border-orange-subtle"
    elif t == "canceled":
        cls = "badge rounded-pill bg-secondary-subtle text-secondary-emphasis border border-secondary-subtle"
    else:
        cls = "badge rounded-pill bg-secondary-subtle text-secondary-emphasis border border-secondary-subtle"
    return f"<span class=\"{cls}\">{text}</span>"


@router.get("/api/queue")
async def queue_data():
    now = time.time()
    twenty_four_hours_ago = now - (24 * 3600)
    capacity = int(os.getenv("CELERY_CONCURRENCY", os.getenv("WEB_CONCURRENCY", "2")) or "2")
    if capacity < 1:
        capacity = 1

    items = []
    status_pairs = []

    jobs_with_scores = await list_jobs_with_scores(500, newest_first=False, min_score=twenty_four_hours_ago, max_score=None)
    for jid, score in jobs_with_scores:
        status = await get_status(jid) or "unknown"
        status_pairs.append((jid, status, score))

    running_slots = 0
    queue_index = 0
    for jid, status, score in status_pairs:
        st = (status or "").lower()
        display_status = status
        is_running_like = st in ("running", "starting")

        if is_running_like:
            running_slots += 1
            if running_slots > capacity:
                display_status = "queued"
                st = "queued"
                queue_index += 1
                queue_pos = queue_index
            else:
                queue_pos = None
        elif st in ("queued", "paused"):
            queue_index += 1
            queue_pos = queue_index
        else:
            queue_pos = None

        prog = await get_progress(jid)
        items.append(
            {
                "job_id": jid,
                "status": status,
                "display_status": display_status,
                "created_at": int(score) if score is not None else None,
                "queue_position": queue_pos,
                "stage": prog.get("stage", ""),
                "pages_total": prog.get("pages_total", ""),
                "pages_done": prog.get("pages_done", ""),
                "pages_failed": prog.get("pages_failed", ""),
                "current": prog.get("current", ""),
                "can_cancel": st in ("queued", "paused", "running", "starting"),
                "can_move": st in ("queued", "paused"),
                "can_pause": st in ("queued", "running", "starting"),
                "can_resume": st == "paused",
            }
        )
    return JSONResponse(items)


@router.get("/api/job/{job_id}")
async def job_data(job_id: str):
    status = await get_status(job_id) or "unknown"
    prog = await get_progress(job_id)
    logs = await get_log(job_id, 300)
    return JSONResponse({"job_id": job_id, "status": status, "progress": prog, "logs": logs})


@router.get("/api/job/{job_id}/delivery-trace")
async def delivery_trace(job_id: str, session: Session = Depends(get_db_session)):
    """
    Debug helper to understand why a completed job did/did not create a delivery_outbox row.
    Avoids returning secrets; only returns safe, high-signal fields.
    """
    status = await get_status(job_id) or "unknown"
    prog = await get_progress(job_id)
    logs = await get_log(job_id, 500)

    needles = (
        "sitemap_uploaded",
        "sitemap_upload_failed",
        "sitemap_saved_db",
        "sitemap_db_save_failed",
        "copy_uploaded",
        "copy_upload_failed",
        "payload_stored",
        "payload_store_failed",
        "outbox_",
        "preview_url_",
    )
    interesting_logs = [line for line in (logs or []) if any(n in line for n in needles)]

    inferred_s3_key = ""
    for line in reversed(logs or []):
        if "copy_uploaded:" in line:
            inferred_s3_key = line.split("copy_uploaded:", 1)[1].strip()
            break
        if "payload_stored:" in line:
            inferred_s3_key = line.split("payload_stored:", 1)[1].strip()
            break

    outbox_row = session.execute(select(DeliveryOutbox).where(DeliveryOutbox.job_id == job_id)).scalars().first()
    outbox = DeliveryOutboxSchema.model_validate(outbox_row).model_dump(mode="json") if outbox_row else None

    s3_key = inferred_s3_key
    if outbox and outbox.get("payload_s3_key"):
        s3_key = str(outbox.get("payload_s3_key") or "").strip()

    s3_head = head_object_info(s3_key) if s3_key else None

    return JSONResponse(
        {
            "job_id": job_id,
            "job_status": status,
            "progress": prog,
            "db": {"location": _safe_db_location()},
            "logs_interesting": interesting_logs[-200:],
            "inferred": {"payload_s3_key": inferred_s3_key},
            "outbox": outbox,
            "s3_head": s3_head,
        }
    )


@router.post("/job/{job_id}/cancel")
async def cancel_job(job_id: str):
    ok = await cancel_queued_job(job_id)
    return JSONResponse({"ok": ok})


@router.post("/job/{job_id}/move")
async def move_job_endpoint(job_id: str, dir: str = Query(default="bottom")):
    ok = await move_job(job_id, dir)
    return JSONResponse({"ok": ok})


@router.post("/job/{job_id}/pause")
async def pause_job_endpoint(job_id: str):
    ok = await pause_job(job_id)
    return JSONResponse({"ok": ok})


@router.post("/job/{job_id}/resume")
async def resume_job_endpoint(job_id: str):
    ok = await resume_job(job_id)
    if ok:
        run_resume_job.delay(job_id)
    return JSONResponse({"ok": ok})


def _get_delivery(session: Session, delivery_id: UUID) -> DeliveryOutbox:
    row = session.get(DeliveryOutbox, delivery_id)
    if row is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    return row


@router.get("/api/deliveries", response_model=DeliveryListResponse)
def list_deliveries(
    status: str | None = Query(default=None),
    client: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
):
    logger.info(
        "ui_list_deliveries_start status=%s client=%s page=%s page_size=%s db=%s",
        status,
        client,
        page,
        page_size,
        _safe_db_location(),
    )
    filters = []
    if status:
        filters.append(DeliveryOutbox.status == status)
    if client:
        filters.append(DeliveryOutbox.client_name.ilike(f"%{client}%"))

    count_stmt = select(func.count()).select_from(DeliveryOutbox)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = session.execute(count_stmt).scalar_one()

    stmt = select(DeliveryOutbox)
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.order_by(DeliveryOutbox.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = session.execute(stmt).scalars().all()

    logger.info(
        "ui_list_deliveries_ok total=%s returned=%s",
        int(total or 0),
        len(items),
    )
    return DeliveryListResponse(
        items=[DeliveryOutboxSchema.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        status_filter=status,
    )


@router.post("/deliveries/{delivery_id}/override-url", response_model=DeliveryOutboxSchema)
def set_override_url(
    delivery_id: UUID,
    payload: OverrideURLRequest,
    session: Session = Depends(get_db_session),
):
    row = _get_delivery(session, delivery_id)
    row.override_target_url = payload.override_target_url
    session.commit()
    session.refresh(row)
    return DeliveryOutboxSchema.model_validate(row)


@router.post("/deliveries/{delivery_id}/send-now", response_model=SendNowResponse)
def send_now(delivery_id: UUID, session: Session = Depends(get_db_session)):
    _get_delivery(session, delivery_id)
    async_result = send_delivery.delay(str(delivery_id))
    return SendNowResponse(ok=True, task_id=async_result.id)


@router.get("/deliveries", response_class=HTMLResponse)
async def deliveries_page():
    html = """
    <html>
      <head>
        <title>Deliveries</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
      </head>
      <body class="bg-light">
        <div class="container py-4">
          <div class="d-flex flex-wrap align-items-center justify-content-between mb-3">
            <div>
              <h2 class="mb-0">Deliveries</h2>
              <div class="text-muted small">/ui/deliveries</div>
            </div>
            <div class="d-flex align-items-center gap-2">
              <a href="/ui/queue" class="btn btn-outline-primary btn-sm">Job Queue</a>
              <button id="refreshBtn" class="btn btn-outline-secondary btn-sm">Refresh</button>
              <div class="text-muted small" id="lastUpdated"></div>
            </div>
          </div>

          <div class="row g-2 mb-3">
            <div class="col-sm-5">
              <input id="clientFilter" class="form-control form-control-sm" placeholder="Filter by client name" />
            </div>
            <div class="col-sm-3">
              <select id="statusFilter" class="form-select form-select-sm">
                <option value="">All statuses</option>
                <option>WAITING_FOR_SITE</option>
                <option>CHECKING_SITE</option>
                <option>READY_TO_SEND</option>
                <option>COMPLETED_PENDING_SEND</option>
                <option>FAILED</option>
                <option>SENDING</option>
                <option>SENT</option>
              </select>
            </div>
            <div class="col-sm-2">
              <button id="applyFilters" class="btn btn-sm btn-primary w-100">Apply</button>
            </div>
          </div>

          <div class="table-responsive">
            <table class="table table-sm align-middle table-hover bg-white shadow-sm">
              <thead class="table-light">
                <tr>
                  <th scope="col">Client</th>
                  <th scope="col">Job ID</th>
                  <th scope="col">Status</th>
                  <th scope="col">Created</th>
                  <th scope="col">Delivery URL</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody id="deliveriesBody">
                <tr><td colspan="6" class="text-muted">Loading…</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <script>
          const body = document.getElementById("deliveriesBody");
          const refreshBtn = document.getElementById("refreshBtn");
          const lastUpdated = document.getElementById("lastUpdated");
          const clientFilter = document.getElementById("clientFilter");
          const statusFilter = document.getElementById("statusFilter");
          const applyFilters = document.getElementById("applyFilters");

          function formatDate(ts){
            if (!ts) return "";
            try {
              const d = new Date(ts);
              return d.toLocaleString();
            } catch (_) {
              return "";
            }
          }

          async function apiJSON(url, opts={}){
            const res = await fetch(url, opts);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
          }

          async function loadDeliveries(){
            body.innerHTML = `<tr><td colspan="6" class="text-muted">Loading…</td></tr>`;
            const params = new URLSearchParams();
            const client = (clientFilter.value || "").trim();
            const status = (statusFilter.value || "").trim();
            if (client) params.set("client", client);
            if (status) params.set("status", status);

            let data;
            try {
              data = await apiJSON(`/ui/api/deliveries?${params.toString()}`);
            } catch (e) {
              body.innerHTML = `<tr><td colspan="6" class="text-danger">Failed to load deliveries.</td></tr>`;
              return;
            }

            const items = data.items || [];
            if (!items.length){
              body.innerHTML = `<tr><td colspan="6" class="text-muted">No deliveries found.</td></tr>`;
              return;
            }

            body.innerHTML = items.map(item => {
              const urlValue = item.override_target_url || "";
              const placeholder = item.default_target_url || "";
              return `
                <tr>
                  <td>${item.client_name || ""}</td>
                  <td class="text-monospace small">${item.job_id || ""}</td>
                  <td>${item.status || ""}</td>
                  <td class="text-muted small">${formatDate(item.created_at)}</td>
                  <td>
                    <input id="delivery-url-${item.id}" class="form-control form-control-sm" placeholder="${placeholder}" value="${urlValue}" />
                  </td>
                  <td class="text-nowrap">
                    <button class="btn btn-sm btn-primary" onclick="sendNow('${item.id}')">Send</button>
                  </td>
                </tr>
              `;
            }).join("");

            lastUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
          }

          async function sendNow(id){
            const input = document.getElementById(`delivery-url-${id}`);
            const url = (input ? input.value : "").trim();
            if (!url){
              alert("Please enter a Delivery URL first.");
              return;
            }
            try {
              await apiJSON(`/ui/deliveries/${id}/override-url`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({override_target_url: url})
              });
              await apiJSON(`/ui/deliveries/${id}/send-now`, { method: "POST" });
              await loadDeliveries();
            } catch (e) {
              alert("Failed to send. Check server logs.");
            }
          }

          refreshBtn.addEventListener("click", loadDeliveries);
          applyFilters.addEventListener("click", loadDeliveries);

          loadDeliveries();
        </script>
      </body>
    </html>
    """
    return HTMLResponse(html)


@router.get("/queue", response_class=HTMLResponse)
async def queue_page():
    html = """
    <html>
      <head>
        <title>Queue</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet" />
      </head>
      <body class="bg-light">
        <div class="container py-4">
          <div class="d-flex flex-wrap align-items-center justify-content-between mb-3">
            <div>
              <h2 class="mb-0">Job Queue</h2>
              <div class="text-muted small">/ui/queue</div>
            </div>
            <div class="d-flex align-items-center gap-2">
              <a href="/ui/deliveries" class="btn btn-outline-primary btn-sm">Deliveries</a>
              <button id="filterBtn" class="btn btn-outline-primary btn-sm" data-bs-toggle="modal" data-bs-target="#filterModal">Filters</button>
              <button id="refreshBtn" class="btn btn-outline-secondary btn-sm">Refresh</button>
              <div class="text-muted small" id="lastUpdated"></div>
            </div>
          </div>

          <div class="text-muted small mb-2">
            Auto-refreshes every 30 seconds. Filters apply on each refresh.
          </div>

          <div class="table-responsive">
            <table class="table table-sm align-middle table-hover bg-white shadow-sm">
              <thead class="table-light">
                <tr>
                  <th scope="col">Job ID</th>
                  <th scope="col">Status</th>
                  <th scope="col">Que #</th>
                  <th scope="col">Stage</th>
                  <th scope="col">Done/Total</th>
                  <th scope="col">Failed</th>
                  <th scope="col">Current</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody id="queueBody">
                <tr><td colspan="8" class="text-muted">Loading…</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Filters Modal -->
        <div class="modal fade" id="filterModal" tabindex="-1" aria-labelledby="filterModalLabel" aria-hidden="true">
          <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
              <div class="modal-header">
                <h5 class="modal-title" id="filterModalLabel">Filters</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
              </div>
              <div class="modal-body">
                <div class="mb-3">
                  <div class="fw-semibold mb-1">Statuses</div>
                  <div class="d-flex flex-wrap gap-2">
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="queued">
                      <span class="form-check-label">Queued</span>
                    </label>
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="running">
                      <span class="form-check-label">Running</span>
                    </label>
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="paused">
                      <span class="form-check-label">Paused</span>
                    </label>
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="completed">
                      <span class="form-check-label">Completed</span>
                    </label>
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="failed">
                      <span class="form-check-label">Failed</span>
                    </label>
                    <label class="form-check form-check-inline mb-0">
                      <input class="form-check-input statusFilter" type="checkbox" value="canceled">
                      <span class="form-check-label">Canceled</span>
                    </label>
                  </div>
                </div>
                <div>
                  <div class="fw-semibold mb-1">Time window</div>
                  <div class="d-flex flex-wrap align-items-center gap-2">
                    <label class="d-flex align-items-center gap-2 mb-0">
                      <span>From</span>
                      <select id="fromHours" class="form-select form-select-sm">
                        <option value="0">Now</option>
                        <option value="1">1h ago</option>
                        <option value="2">2h ago</option>
                        <option value="4">4h ago</option>
                        <option value="6">6h ago</option>
                        <option value="12">12h ago</option>
                        <option value="24">24h ago</option>
                      </select>
                    </label>
                    <span>to</span>
                    <label class="d-flex align-items-center gap-2 mb-0">
                      <select id="toHours" class="form-select form-select-sm">
                        <option value="">Any time</option>
                        <option value="1">1h ago</option>
                        <option value="2">2h ago</option>
                        <option value="4">4h ago</option>
                        <option value="6">6h ago</option>
                        <option value="12">12h ago</option>
                        <option value="24">24h ago</option>
                      </select>
                    </label>
                  </div>
                </div>
              </div>
              <div class="modal-footer">
                <button type="button" class="btn btn-outline-secondary btn-sm" id="clearFilters">Clear all</button>
                <button type="button" class="btn btn-primary btn-sm" id="applyFilters" data-bs-dismiss="modal">Apply</button>
              </div>
            </div>
          </div>
        </div>

        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
        <script>
          // Badges
          const badgeHTML = (text) => {
            const t = (text || "unknown").toLowerCase();
            let bg="#f3f4f6", fg="#374151", bd="#d1d5db";
            if (t==="completed"){bg="#dcfce7";fg="#166534";bd="#86efac";}
            else if (t==="running"){bg="#dbeafe";fg="#1e40af";bd="#93c5fd";}
            else if (t==="failed"){bg="#fee2e2";fg="#991b1b";bd="#fca5a5";}
            else if (t==="queued"){bg="#fef9c3";fg="#854d0e";bd="#fde047";}
            else if (t==="paused"){bg="#fff7ed";fg="#9a3412";bd="#fdba74";}
            return `<span style="display:inline-block;padding:2px 10px;border-radius:999px;border:1px solid ${bd};background:${bg};color:${fg};font-size:12px;line-height:18px">${text}</span>`;
          };

          const safe = (v) => (v === null || v === undefined) ? "" : String(v);

          const FILTER_STORAGE_KEY = "queueFiltersV1";
          const DEFAULT_FILTERS = {
            statuses: ["queued", "running", "paused", "completed", "failed", "canceled"],
            fromH: "0",
            toH: "",
          };

          function loadFilters() {
            try {
              const raw = localStorage.getItem(FILTER_STORAGE_KEY);
              if (!raw) return { ...DEFAULT_FILTERS };
              const parsed = JSON.parse(raw);
              return {
                statuses: Array.isArray(parsed.statuses) ? parsed.statuses : DEFAULT_FILTERS.statuses,
                fromH: parsed.fromH ?? DEFAULT_FILTERS.fromH,
                toH: parsed.toH ?? DEFAULT_FILTERS.toH,
              };
            } catch (_) {
              return { ...DEFAULT_FILTERS };
            }
          }

          function saveFilters(filters) {
            try {
              localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(filters));
            } catch (_) {}
          }

          function setFiltersUI(filters) {
            const statusSet = new Set(filters.statuses || []);
            document.querySelectorAll(".statusFilter").forEach((el) => {
              el.checked = statusSet.has(el.value);
            });
            document.getElementById("fromHours").value = filters.fromH ?? DEFAULT_FILTERS.fromH;
            document.getElementById("toHours").value = filters.toH ?? DEFAULT_FILTERS.toH;
          }

          function getSelectedStatuses() {
            return Array.from(document.querySelectorAll(".statusFilter:checked")).map(el => el.value);
          }

          function currentFilters() {
            const statuses = getSelectedStatuses();
            const fromH = document.getElementById("fromHours").value;
            const toH = document.getElementById("toHours").value;
            return { statuses, fromH, toH };
          }

          async function apiJSON(url, opts) {
            const res = await fetch(url, opts || {});
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
          }

          let allItems = [];

          function renderFilteredItems() {
            const body = document.getElementById("queueBody");
            const { statuses, fromH, toH } = currentFilters();
            const statusSet = new Set(statuses.map(s => s.toLowerCase()));
            const nowSec = Date.now() / 1000;
            const fromVal = fromH === "" ? null : parseFloat(fromH);
            const toVal = toH === "" ? null : parseFloat(toH);

            const filtered = allItems.filter(item => {
              const st = (item.display_status || item.status || "").toLowerCase();
              if (statusSet.size && !statusSet.has(st)) return false;
              const createdAt = item.created_at || 0;
              const hoursAgo = (nowSec - createdAt) / 3600;
              if (fromVal !== null && hoursAgo < fromVal) return false;
              if (toVal !== null && hoursAgo > toVal) return false;
              return true;
            });

            if (!filtered.length) {
              body.innerHTML = `<tr><td colspan="8" style="color:#6b7280;">No jobs found.</td></tr>`;
              return;
            }

            body.innerHTML = filtered.map(item => {
              const jid = item.job_id;
              const status = item.display_status || item.status || "unknown";
              const queuePos = item.queue_position || "";
              const stage = safe(item.stage);
              const total = safe(item.pages_total);
              const done = safe(item.pages_done);
              const failed = safe(item.pages_failed);
              const current = safe(item.current);
              return `
                <tr>
                  <td style="font-family:ui-monospace, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size:12px;">
                    <a href="/ui/job/${jid}">${jid}</a>
                  </td>
                  <td>${badgeHTML(status)}</td>
                  <td>${queuePos}</td>
                  <td>${stage}</td>
                  <td>${done}/${total}</td>
                  <td>${failed}</td>
                  <td style="max-width:420px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${current}</td>
                  <td>${actionsHTML(item)}</td>
                </tr>
              `;
            }).join("");
          }

          async function cancelJob(jobId) {
            await apiJSON(`/ui/job/${jobId}/cancel`, { method: "POST" });
            await refreshQueue();
          }

          async function pauseJob(jobId) {
            await apiJSON(`/ui/job/${jobId}/pause`, { method: "POST" });
            await refreshQueue();
          }

          async function resumeJob(jobId) {
            await apiJSON(`/ui/job/${jobId}/resume`, { method: "POST" });
            await refreshQueue();
          }

          async function moveJob(jobId, dir) {
            await apiJSON(`/ui/job/${jobId}/move?dir=${encodeURIComponent(dir)}`, { method: "POST" });
            await refreshQueue();
          }

          // --- Icons (Bootstrap Icons) ---
          const ICONS = {
            cancel: `<i class="bi bi-x-lg"></i>`,
            pause: `<i class="bi bi-pause-fill"></i>`,
            resume: `<i class="bi bi-play-fill"></i>`,
            up: `<i class="bi bi-chevron-up"></i>`,
            down: `<i class="bi bi-chevron-down"></i>`,
            top: `<i class="bi bi-arrow-up-square"></i>`,
            bottom: `<i class="bi bi-arrow-down-square"></i>`,
          };

          const iconBtnStyle = (enabled) => `
            width:40px;height:40px;
            display:inline-flex;align-items:center;justify-content:center;
            padding:0;
          `;

          const iconWrapStyle = `
            width:22px;height:22px;display:block;font-size:20px;font-weight:700;line-height:1;
          `;

          function iconButton({ title, enabled, onClick, iconKey }) {
            const disabledAttr = enabled ? "" : "disabled";
            const handlerAttr = enabled ? `onclick="${onClick}"` : "";
            return `
              <button title="${title}" aria-label="${title}"
                class="btn btn-sm ${enabled ? 'btn-outline-secondary' : 'btn-outline-secondary disabled'}"
                style="${iconBtnStyle(enabled)}" ${disabledAttr} ${handlerAttr}
                data-bs-toggle="tooltip" data-bs-placement="top" data-bs-title="${title}">
                <span style="${iconWrapStyle}">${ICONS[iconKey]}</span>
              </button>
            `;
          }

          function actionsHTML(item) {
            const jid = item.job_id;

            const cancelBtn = iconButton({
              title: "Cancel",
              enabled: !!item.can_cancel,
              iconKey: "cancel",
              onClick: `cancelJob('${jid}')`,
            });

            const pauseResumeBtn = item.can_pause
              ? iconButton({
                  title: "Pause",
                  enabled: true,
                  iconKey: "pause",
                  onClick: `pauseJob('${jid}')`,
                })
              : (item.can_resume
                  ? iconButton({
                      title: "Resume",
                      enabled: true,
                      iconKey: "resume",
                      onClick: `resumeJob('${jid}')`,
                    })
                  : iconButton({
                      title: "Pause",
                      enabled: false,
                      iconKey: "pause",
                      onClick: "",
                    })
                );

            const moveTop = iconButton({
              title: "Move to top",
              enabled: !!item.can_move,
              iconKey: "top",
              onClick: `moveJob('${jid}','top')`,
            });

            const moveUp = iconButton({
              title: "Move up",
              enabled: !!item.can_move,
              iconKey: "up",
              onClick: `moveJob('${jid}','up')`,
            });

            const moveDown = iconButton({
              title: "Move down",
              enabled: !!item.can_move,
              iconKey: "down",
              onClick: `moveJob('${jid}','down')`,
            });

            const moveBottom = iconButton({
              title: "Move to bottom",
              enabled: !!item.can_move,
              iconKey: "bottom",
              onClick: `moveJob('${jid}','bottom')`,
            });

            return `
              <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
                ${cancelBtn}
                ${pauseResumeBtn}
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                  ${moveTop}
                  ${moveUp}
                  ${moveDown}
                  ${moveBottom}
                </div>
              </div>
            `;
          }

          async function refreshQueue() {
            const body = document.getElementById("queueBody");
            try {
              allItems = await apiJSON(`/ui/api/queue`);
              renderFilteredItems();
              const now = new Date();
              document.getElementById("lastUpdated").textContent = `Last updated: ${now.toLocaleTimeString()}`;
            } catch (e) {
              body.innerHTML = `<tr><td colspan="8" style="color:#ef4444;">Failed to load queue data.</td></tr>`;
            }
          }

          document.getElementById("refreshBtn").addEventListener("click", () => refreshQueue());
          document.getElementById("applyFilters").addEventListener("click", () => {
            const filters = currentFilters();
            saveFilters(filters);
            renderFilteredItems();
            refreshQueue();
          });
          document.getElementById("clearFilters").addEventListener("click", () => {
            setFiltersUI(DEFAULT_FILTERS);
            saveFilters(DEFAULT_FILTERS);
            renderFilteredItems();
          });
          document.getElementById("filterModal").addEventListener("show.bs.modal", () => {
            const filters = loadFilters();
            setFiltersUI(filters);
          });
          const tooltips = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]')).map(el => new bootstrap.Tooltip(el));
          window.cancelJob = cancelJob;
          window.pauseJob = pauseJob;
          window.resumeJob = resumeJob;
          window.moveJob = moveJob;

          setFiltersUI(loadFilters());
          refreshQueue();
          setInterval(refreshQueue, 30000);
        </script>
      </body>
    </html>
    """
    return html


@router.get("/job/{job_id}", response_class=HTMLResponse)
async def job_page(job_id: str):
    status = await get_status(job_id) or "unknown"
    prog = await get_progress(job_id)
    logs = await get_log(job_id, 300)

    stage = prog.get("stage", "")
    total = prog.get("pages_total", "")
    done = prog.get("pages_done", "")
    failed = prog.get("pages_failed", "")
    skipped = prog.get("pages_skipped", "")
    current = prog.get("current", "")
    log_text = "\n".join(logs) if logs else ""

    def _level_of(line: str) -> str:
        if isinstance(line, str) and len(line) >= 3 and line.startswith("[") and line[2] == "]":
            return line[1].upper()
        return "I"

    simple_logs: list[str] = []
    debug_logs: list[str] = []
    for line in logs or []:
        lvl = _level_of(line)
        if lvl == "D":
            debug_logs.append(line)
        else:
            simple_logs.append(line)

    simple_log_text = "\n".join(simple_logs)
    full_log_text = log_text
    thread_run_lines = []
    seen_pairs = set()
    for line in logs or []:
        if ("thread_id" in line) or ("run_id" in line):
            if line not in seen_pairs:
                thread_run_lines.append(line)
                seen_pairs.add(line)

    full_logs_json = json.dumps(full_log_text)
    simple_logs_json = json.dumps(simple_log_text)

    if thread_run_lines:
        thread_items = "".join([f"<li class='list-group-item py-1 px-2'>{line}</li>" for line in thread_run_lines])
        thread_run_section = f"""
          <div class="card mt-3">
            <div class="card-header d-flex align-items-center justify-content-between">
              <span>Thread/Run IDs found in logs</span>
              <button class="btn btn-sm btn-outline-secondary" type="button" data-bs-toggle="collapse" data-bs-target="#threadRunCollapse" aria-expanded="false" aria-controls="threadRunCollapse">
                Show/Hide
              </button>
            </div>
            <div class="collapse" id="threadRunCollapse">
              <div class="card-body p-0">
                <ul class="list-group list-group-flush small">
                  {thread_items}
                </ul>
              </div>
            </div>
          </div>
        """
    else:
        thread_run_section = """
          <div class="alert alert-secondary mt-3 mb-0 py-2 small">
            No thread/run IDs found in the last 300 log lines.
          </div>
        """

    html = f"""
    <html>
      <head>
        <title>Job {job_id}</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet" />
      </head>
      <body class="bg-light">
        <div class="container py-4">
          <div class="d-flex align-items-center justify-content-between mb-3">
            <div>
              <div class="small text-muted"><a href="/ui/queue">← Back to queue</a></div>
              <h2 class="mb-1">Job</h2>
              <div class="text-monospace small text-dark">{job_id}</div>
            </div>
            <div>{_badge(status)}</div>
          </div>

          <div class="card mb-3">
            <div class="card-body d-flex flex-wrap gap-3 small">
              <div><strong>Stage:</strong> {stage}</div>
              <div><strong>Done/Total:</strong> {done}/{total}</div>
              <div><strong>Failed:</strong> {failed}</div>
              <div><strong>Skipped:</strong> {skipped}</div>
              <div class="text-truncate" style="max-width: 600px;"><strong>Current:</strong> {current}</div>
            </div>
          </div>

          <div class="card mb-3">
            <div class="card-header d-flex align-items-center justify-content-between">
              <h5 class="mb-0">Progress JSON</h5>
              <a href="/result/{job_id}" class="small">View result JSON</a>
            </div>
            <div class="card-body">
              <pre class="bg-dark text-light p-3 rounded small mb-0" style="overflow:auto;">{prog}</pre>
            </div>
          </div>

          <div class="card">
            <div class="card-header">
              <div class="d-flex align-items-center justify-content-between">
                <h5 class="mb-0">Logs (last 300)</h5>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="debugToggle">
                  <label class="form-check-label" for="debugToggle">Debugging</label>
                </div>
              </div>
            </div>
            <div class="card-body">
              {thread_run_section}
              <pre id="logText" class="bg-dark text-success p-3 rounded small mt-3 mb-0" style="overflow:auto; white-space:pre-wrap;">{simple_log_text}</pre>
            </div>
          </div>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
        <script>
          const fullLogs = {full_logs_json};
          const simpleLogs = {simple_logs_json};
          const logPre = document.getElementById("logText");
          const debugToggle = document.getElementById("debugToggle");

          function updateLogs() {{
            const useDebug = debugToggle.checked;
            logPre.textContent = useDebug ? fullLogs : simpleLogs;
          }}
          debugToggle.addEventListener("change", updateLogs);
          updateLogs();
        </script>
      </body>
    </html>
    """
    return html
