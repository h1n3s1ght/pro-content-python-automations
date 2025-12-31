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
        <script>
        // --- Icon SVGs (inline) ---
        const ICONS = {
            cancel: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
            <path fill="none" d="M0 0h24v24H0V0z" opacity=".87"></path>
            <path d="M12 2C6.47 2 2 6.47 2 12s4.47 10 10 10 10-4.47 10-10S17.53 2 12 2zm4.3 14.3c-.39.39-1.02.39-1.41 0L12 13.41 9.11 16.3c-.39.39-1.02.39-1.41 0-.39-.39-.39-1.02 0-1.41L10.59 12 7.7 9.11c-.39-.39-.39-1.02 0-1.41.39-.39 1.02-.39 1.41 0L12 10.59l2.89-2.89c.39-.39 1.02-.39 1.41 0 .39.39.39 1.02 0 1.41L13.41 12l2.89 2.89c.38.38.38 1.02 0 1.41z"></path>
            </svg>`,
            pause: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1zm4 0c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1z"></path>
            </svg>`,
            up: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
            <path fill="currentColor" fill-rule="evenodd" d="M3.5 12C3.5 16.6944 7.30558 20.5 12 20.5C16.6944 20.5 20.5 16.6944 20.5 12C20.5 7.30558 16.6944 3.5 12 3.5C7.30558 3.5 3.5 7.30558 3.5 12ZM12 21.5C6.75329 21.5 2.5 17.2467 2.5 12C2.5 6.75329 6.7533 2.5 12 2.5C17.2467 2.5 21.5 6.7533 21.5 12C21.5 17.2467 17.2467 21.5 12 21.5Z" clip-rule="evenodd"></path>
            <path fill="currentColor" fill-rule="evenodd" d="M12 17.5C11.7239 17.5 11.5 17.2761 11.5 17L11.5 7.5C11.5 7.22386 11.7239 7 12 7C12.2761 7 12.5 7.22386 12.5 7.5L12.5 17C12.5 17.2761 12.2761 17.5 12 17.5Z" clip-rule="evenodd"></path>
            <path fill="currentColor" fill-rule="evenodd" d="M8.14645 10.8536C7.95118 10.6583 7.95118 10.3417 8.14645 10.1464L11.6464 6.64645C11.8417 6.45118 12.1583 6.45118 12.3536 6.64645L15.8536 10.1464C16.0488 10.3417 16.0488 10.6583 15.8536 10.8536C15.6583 11.0488 15.3417 11.0488 15.1464 10.8536L12 7.70711L8.85355 10.8536C8.65829 11.0488 8.34171 11.0488 8.14645 10.8536Z" clip-rule="evenodd"></path>
            </svg>`,
            down: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" aria-hidden="true">
            <path d="M50,95.8C24.8,95.8,4.3,75.2,4.3,50S24.8,4.3,50,4.3S95.8,24.8,95.8,50S75.2,95.8,50,95.8z M50,9.3
                C27.5,9.3,9.3,27.5,9.3,50c0,22.5,18.3,40.8,40.7,40.8c22.5,0,40.8-18.3,40.8-40.8C90.8,27.5,72.5,9.3,50,9.3z"></path>
            <path d="M52.5,70.1V29.9c0-1.4-1.1-2.5-2.5-2.5s-2.5,1.1-2.5,2.5v40.3c0,1.4,1.1,2.5,2.5,2.5S52.5,71.5,52.5,70.1z"></path>
            <path d="M69.7,53c0-0.6-0.2-1.3-0.7-1.8c-1-1-2.6-1-3.5,0L50,66.6L34.6,51.2c-1-1-2.6-1-3.5,0c-1,1-1,2.6,0,3.5
                l17.2,17.2c0.5,0.5,1.1,0.7,1.8,0.7s1.3-0.3,1.8-0.7l17.2-17.2C69.4,54.2,69.7,53.6,69.7,53z"></path>
            </svg>`,
            top: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12.71,6.29a1,1,0,0,0-.33-.21,1,1,0,0,0-.76,0,1,1,0,0,0-.33.21l-4,4a1,1,0,1,0,1.42,1.42L11,9.41V21a1,1,0,0,0,2,0V9.41l2.29,2.3a1,1,0,0,0,1.42,0,1,1,0,0,0,0-1.42ZM19,2H5A1,1,0,0,0,5,4H19a1,1,0,0,0,0-2Z"></path>
            </svg>`,
            bottom: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
            <path fill="none" d="M0 0h24v24H0z"></path>
            <path stroke="currentColor" stroke-linecap="round" stroke-width="1.5" d="M12 5 12 17M16 13 12 17M8 13 12 17M16 19H8"></path>
            </svg>`,
            // Optional: if you want a distinct Resume icon, you can swap this later.
            resume: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1.2 6.8v6.4L16 12l-5.2-3.2z"></path>
            </svg>`
        };

        // --- Button helpers ---
        const iconBtnStyle = (enabled) => `
            width:34px;height:34px;
            display:inline-flex;align-items:center;justify-content:center;
            border:1px solid #e5e7eb;border-radius:10px;
            background:${enabled ? "#fff" : "#f3f4f6"};
            color:${enabled ? "#111827" : "#9ca3af"};
            cursor:${enabled ? "pointer" : "not-allowed"};
            padding:0;
        `;

        const iconSvgStyle = `
            width:18px;height:18px;display:block;
        `;

        function iconButton({ title, enabled, onClick, iconKey }) {
            const disabledAttr = enabled ? "" : "disabled";
            const handlerAttr = enabled ? `onclick="${onClick}"` : "";
            // Wrap with title for hover tooltip
            return `
            <button title="${title}" aria-label="${title}"
                style="${iconBtnStyle(enabled)}" ${disabledAttr} ${handlerAttr}>
                <span style="${iconSvgStyle}">
                ${ICONS[iconKey]}
                </span>
            </button>
            `;
        }

        // --- Replace your existing actionsHTML with this ---
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
                    iconKey: "resume", // or swap to "pause" if you want same icon
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
        </script>

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
