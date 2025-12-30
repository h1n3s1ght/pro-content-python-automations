from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import Query

from .storage import (
    list_jobs,
    get_status,
    get_progress,
    get_log,
    cancel_queued_job,
    move_job,
    pause_job,
    resume_job,
)

router = APIRouter(prefix="/ui", tags=["ui"])


def _badge(text: str) -> str:
    t = (text or "unknown").lower()
    if t == "completed":
        bg = "#dcfce7"
        fg = "#166534"
        bd = "#86efac"
    elif t == "running":
        bg = "#dbeafe"
        fg = "#1e40af"
        bd = "#93c5fd"
    elif t == "failed":
        bg = "#fee2e2"
        fg = "#991b1b"
        bd = "#fca5a5"
    elif t == "queued":
        bg = "#fef9c3"
        fg = "#854d0e"
        bd = "#fde047"
    elif t == "paused":
        bg = "#fff7ed"
        fg = "#9a3412"
        bd = "#fdba74"
    elif t == "canceled":
        bg = "#f3f4f6"
        fg = "#374151"
        bd = "#d1d5db"
    else:
        bg = "#f3f4f6"
        fg = "#374151"
        bd = "#d1d5db"
    return f"<span style='display:inline-block;padding:2px 10px;border-radius:999px;border:1px solid {bd};background:{bg};color:{fg};font-size:12px;line-height:18px'>{text}</span>"


@router.get("/api/queue")
async def queue_data():
    job_ids = await list_jobs(200, newest_first=False)
    items = []
    for jid in job_ids:
        status = await get_status(jid) or "unknown"
        prog = await get_progress(jid)
        st = (status or "").lower()
        items.append(
            {
                "job_id": jid,
                "status": status,
                "stage": prog.get("stage", ""),
                "pages_total": prog.get("pages_total", ""),
                "pages_done": prog.get("pages_done", ""),
                "pages_failed": prog.get("pages_failed", ""),
                "current": prog.get("current", ""),
                "can_cancel": st in ("queued", "paused"),
                "can_move": st in ("queued", "paused"),
                "can_pause": st == "queued",
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
    return JSONResponse({"ok": ok})


@router.get("/queue", response_class=HTMLResponse)
async def queue_page():
    html = """
    <html>
      <head>
        <title>Queue</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
      </head>
      <body style="font-family:system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; padding:20px; background:#fff;">
        <div style="display:flex; align-items:center; justify-content:space-between;">
          <div>
            <h2 style="margin:0;">Job Queue</h2>
            <div style="font-size:12px; color:#6b7280; margin-top:4px;">/ui/queue</div>
          </div>
          <div style="display:flex; gap:10px; align-items:center;">
            <button id="refreshBtn" style="padding:8px 12px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;cursor:pointer;">
              Refresh
            </button>
            <div style="font-size:12px; color:#6b7280;" id="lastUpdated"></div>
          </div>
        </div>

        <div style="margin-top:12px; margin-bottom:12px; font-size:13px; color:#374151;">
          Auto-refreshes every 30 seconds.
        </div>

        <table cellpadding="10" cellspacing="0" style="border-collapse:collapse; width:100%; border:1px solid #e5e7eb;">
          <thead>
            <tr style="background:#f9fafb; text-align:left; font-size:13px; color:#111827;">
              <th>Job ID</th>
              <th>Status</th>
              <th>Stage</th>
              <th>Done/Total</th>
              <th>Failed</th>
              <th>Current</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="queueBody" style="font-size:13px; color:#111827;">
            <tr><td colspan="7" style="color:#6b7280;">Loading…</td></tr>
          </tbody>
        </table>

        <script>
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

          async function apiJSON(url, opts) {
            const res = await fetch(url, opts || {});
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
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

          function actionsHTML(item) {
            const cancelBtn = item.can_cancel
              ? `<button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;cursor:pointer;" onclick="cancelJob('${item.job_id}')">Cancel</button>`
              : `<button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#f3f4f6;color:#9ca3af;cursor:not-allowed;" disabled>Cancel</button>`;

            const pauseResumeBtn = item.can_pause
              ? `<button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;cursor:pointer;" onclick="pauseJob('${item.job_id}')">Pause</button>`
              : (item.can_resume
                ? `<button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;cursor:pointer;" onclick="resumeJob('${item.job_id}')">Resume</button>`
                : `<button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#f3f4f6;color:#9ca3af;cursor:not-allowed;" disabled>Pause</button>`);

            const moveBtns = item.can_move ? `
              <div style="display:flex;gap:6px;flex-wrap:wrap;">
                <button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;cursor:pointer;" onclick="moveJob('${item.job_id}','top')">Top</button>
                <button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;cursor:pointer;" onclick="moveJob('${item.job_id}','up')">Up</button>
                <button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;cursor:pointer;" onclick="moveJob('${item.job_id}','down')">Down</button>
                <button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;cursor:pointer;" onclick="moveJob('${item.job_id}','bottom')">Bottom</button>
              </div>
            ` : `
              <div style="display:flex;gap:6px;flex-wrap:wrap;">
                <button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#f3f4f6;color:#9ca3af;cursor:not-allowed;" disabled>Top</button>
                <button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#f3f4f6;color:#9ca3af;cursor:not-allowed;" disabled>Up</button>
                <button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#f3f4f6;color:#9ca3af;cursor:not-allowed;" disabled>Down</button>
                <button style="padding:6px 10px;border:1px solid #e5e7eb;border-radius:10px;background:#f3f4f6;color:#9ca3af;cursor:not-allowed;" disabled>Bottom</button>
              </div>
            `;

            return `<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">${cancelBtn}${pauseResumeBtn}${moveBtns}</div>`;
          }

          async function refreshQueue() {
            const body = document.getElementById("queueBody");
            try {
              const items = await apiJSON("/ui/api/queue");
              if (!items.length) {
                body.innerHTML = `<tr><td colspan="7" style="color:#6b7280;">No jobs found.</td></tr>`;
              } else {
                body.innerHTML = items.map(item => {
                  const jid = item.job_id;
                  const status = item.status || "unknown";
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
                      <td>${stage}</td>
                      <td>${done}/${total}</td>
                      <td>${failed}</td>
                      <td style="max-width:420px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${current}</td>
                      <td>${actionsHTML(item)}</td>
                    </tr>
                  `;
                }).join("");
              }
              const now = new Date();
              document.getElementById("lastUpdated").textContent = `Last updated: ${now.toLocaleTimeString()}`;
            } catch (e) {
              body.innerHTML = `<tr><td colspan="7" style="color:#ef4444;">Failed to load queue data.</td></tr>`;
            }
          }

          document.getElementById("refreshBtn").addEventListener("click", () => refreshQueue());
          window.cancelJob = cancelJob;
          window.pauseJob = pauseJob;
          window.resumeJob = resumeJob;
          window.moveJob = moveJob;

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

    html = f"""
    <html>
      <head>
        <title>Job {job_id}</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
      </head>
      <body style="font-family:system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; padding:20px; background:#fff;">
        <div style="display:flex; align-items:center; justify-content:space-between;">
          <div>
            <div style="font-size:12px; color:#6b7280;"><a href="/ui/queue">← Back to queue</a></div>
            <h2 style="margin:6px 0 0 0;">Job</h2>
            <div style="font-family:ui-monospace, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size:12px; color:#111827;">
              {job_id}
            </div>
          </div>
          <div>{_badge(status)}</div>
        </div>

        <div style="margin-top:14px; padding:12px; border:1px solid #e5e7eb; border-radius:10px; background:#f9fafb;">
          <div style="display:flex; gap:18px; flex-wrap:wrap; font-size:13px; color:#111827;">
            <div><strong>Stage:</strong> {stage}</div>
            <div><strong>Done/Total:</strong> {done}/{total}</div>
            <div><strong>Failed:</strong> {failed}</div>
            <div><strong>Skipped:</strong> {skipped}</div>
            <div style="max-width:600px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
              <strong>Current:</strong> {current}
            </div>
          </div>
        </div>

        <div style="margin-top:14px;">
          <div style="display:flex; align-items:center; justify-content:space-between;">
            <h3 style="margin:0;">Progress JSON</h3>
            <a href="/result/{job_id}" style="font-size:13px;">View result JSON</a>
          </div>
          <pre style="margin-top:8px; background:#111827; color:#e5e7eb; padding:12px; border-radius:10px; overflow:auto; font-size:12px;">{prog}</pre>
        </div>

        <div style="margin-top:14px;">
          <h3 style="margin:0;">Logs (last 300)</h3>
          <pre style="margin-top:8px; background:#0b1020; color:#a7f3d0; padding:12px; border-radius:10px; overflow:auto; font-size:12px; white-space:pre-wrap;">{log_text}</pre>
        </div>
      </body>
    </html>
    """
    return html
