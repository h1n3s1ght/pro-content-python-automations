from __future__ import annotations

from datetime import datetime
from typing import Dict, Any, List

from .storage import get_monthly_queue_logs, clear_monthly_queue_logs
from .s3_upload import upload_monthly_logs


def parse_month(month_yyyy_mm: str) -> tuple[str, int]:
    dt = datetime.strptime(month_yyyy_mm + "-01", "%Y-%m-%d")
    return dt.strftime("%B"), dt.year


async def upload_monthly_queue_logs(month_yyyy_mm: str) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = await get_monthly_queue_logs(month_yyyy_mm)
    month_name, year = parse_month(month_yyyy_mm)

    payload = {"month": month_yyyy_mm, "jobs": items}
    s3_key = upload_monthly_logs(month_name=month_name, year=year, logs=payload)

    await clear_monthly_queue_logs(month_yyyy_mm)

    return {"uploaded": True, "s3_key": s3_key, "jobs": len(items)}
