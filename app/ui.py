from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .storage import list_jobs, get_status, get_progress, get_log

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
    else:
        bg = "#f3f4f6"
        fg = "#374151"
        bd = "#d1d5db"
    return f"<span style='display:inline-block;padding:2px 10px;border-radius:999px;border:1px solid {bd};background:{bg};color:{fg};font-size:12px;line-height:18px'>{text}</span>"


def _safe(v):
    return "" if v is None else str(v)


@router.get("/queue", response_class=HTMLResponse)
async def queue_page():
    job_ids = await list_jobs(200, newest_first=False)
    rows = []
    for jid in job_ids:
        status = await get_status(jid) or "unknown"
        prog = await get_progress(jid)
        stage = _safe(prog.get("stage"))
        total = _safe(prog.get("pages_total"))
        done = _safe(prog.get("pages_done"))
        failed = _safe(prog.get("pages_failed"))
        current = _safe(prog.get("current"))
        rows.append(
            f"""
            <tr>
              <td style="font-family:ui-monospace, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size:12px;">
                <a href="/ui/job/{jid}">{jid}</a>
              </td>
              <td>{_badge(status)}</td>
              <td>{stage}</td>
              <td>{done}/{total}</td>
              <td>{failed}</td>
              <td style="max-width:420px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{current}</td>
            </tr>
            """
        )

    html = f"""
    <html>
      <head>
        <title>Queue</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
      </head>
      <body style="font-family:system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; padding:20px; background:#fff;">
        <div style="display:flex; align-items:center; justify-content:space-between;">
          <h2 style="margin:0;">Job Queue</h2>
          <div style="font-size:12px; color:#6b7280;">/ui/queue</div>
        </div>
        <div style="margin-top:12px; margin-bottom:12px; font-size:13px; color:#374151;">
          Refresh the page to see updates.
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
            </tr>
          </thead>
          <tbody style="font-size:13px; color:#111827;">
            {''.join(rows) if rows else "<tr><td colspan='6' style='color:#6b7280;'>No jobs found.</td></tr>"}
          </tbody>
        </table>
      </body>
    </html>
    """
    return html


@router.get("/job/{job_id}", response_class=HTMLResponse)
async def job_page(job_id: str):
    status = await get_status(job_id) or "unknown"
    prog = await get_progress(job_id)
    logs = await get_log(job_id, 300)

    stage = _safe(prog.get("stage"))
    total = _safe(prog.get("pages_total", 0))
    done = _safe(prog.get("pages_done", 0))
    failed = _safe(prog.get("pages_failed", 0))
    current = _safe(prog.get("current", ""))
    skipped = _safe(prog.get("pages_skipped"))

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
