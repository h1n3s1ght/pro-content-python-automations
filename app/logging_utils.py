from __future__ import annotations

from typing import Literal

from .storage import append_log

LogLevel = Literal["I", "D", "W", "E", "*"]


def format_log(level: LogLevel, message: str) -> str:
    l = (level or "I").upper()
    code = l if l in {"I", "D", "W", "E", "*"} else "I"
    return f"[{code}] {message}"


async def log_line(job_id: str, level: LogLevel, message: str) -> None:
    await append_log(job_id, format_log(level, message))


async def log_info(job_id: str, message: str) -> None:
    await log_line(job_id, "I", message)


async def log_debug(job_id: str, message: str) -> None:
    await log_line(job_id, "D", message)


async def log_warn(job_id: str, message: str) -> None:
    await log_line(job_id, "W", message)


async def log_error(job_id: str, message: str) -> None:
    await log_line(job_id, "E", message)
