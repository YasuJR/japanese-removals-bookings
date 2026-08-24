"""Daily Jobs page — all bookings on one calendar day."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import database as db
import job_status
from booking_times import (
    duration_hours_from_times,
    effective_finish_hm,
    effective_start_hm,
    format_time_12h,
    normalize_time_input,
)
from crew import crew_from_storage
from display_dates import normalize_move_date


def format_job_duration_label(hours: Optional[float]) -> str:
    """Display hours as 2hr / 2.5hr / 2.25hr from Start–Finish duration."""
    if hours is None:
        return ""
    try:
        value = float(hours)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    rounded = round(value, 2)
    if abs(rounded - int(rounded)) < 1e-9:
        text = str(int(rounded))
    else:
        text = "{0:.2f}".format(rounded).rstrip("0").rstrip(".")
    return "{0}hr".format(text)


def _time_to_minutes(hm: str) -> int:
    parts = (hm or "08:00").split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (IndexError, ValueError):
        return 8 * 60


def _crew_slash_display(booking: Dict[str, Any]) -> str:
    names = crew_from_storage(booking.get("crew"))
    return " / ".join(names) if names else "—"


def _date_heading(date_iso: str) -> str:
    try:
        return datetime.strptime(date_iso[:10], "%Y-%m-%d").strftime("%A, %d %B")
    except ValueError:
        return date_iso


def _serialize_job(booking: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(booking)
    iso = normalize_move_date(row.get("move_date"))
    if iso:
        row["move_date"] = iso
    start_hm = effective_start_hm(row)
    finish_hm = normalize_time_input(row.get("finish_time")) or effective_finish_hm(row)
    start_display = format_time_12h(start_hm)
    finish_display = format_time_12h(finish_hm)
    duration_hours = duration_hours_from_times(start_hm, finish_hm)
    crew_list = crew_from_storage(row.get("crew"))
    return {
        "id": int(row["id"]),
        "customer_name": (row.get("customer_name") or "—").strip(),
        "phone": (row.get("phone") or "").strip(),
        "pickup_address": (row.get("pickup_address") or "—").strip(),
        "delivery_address": (row.get("delivery_address") or "—").strip(),
        "notes": (row.get("notes") or "").strip(),
        "status": job_status.display(row),
        "num_movers": int(row.get("num_movers") or 0),
        "crew_display": _crew_slash_display(row),
        "crew_list": crew_list,
        "time_range": "{0} – {1}".format(start_display, finish_display),
        "duration_label": format_job_duration_label(duration_hours),
        "start_display": start_display,
        "finish_display": finish_display,
        "start_minutes": _time_to_minutes(start_hm),
        "finish_minutes": _time_to_minutes(finish_hm),
    }


def build_daily_jobs(date_iso: str) -> Dict[str, Any]:
    """Load bookings for one day, sorted by start time."""
    date_iso = normalize_move_date(date_iso) or (date_iso or "").strip()
    rows = db.list_by_date(date_iso)
    jobs = [_serialize_job(dict(row)) for row in rows]
    jobs.sort(key=lambda job: (job["start_minutes"], job["customer_name"].lower()))

    for index, job in enumerate(jobs, start=1):
        job["job_number"] = index
        job["job_label"] = "JOB {0}".format(index)

    crews: set = set()
    for job in jobs:
        crews.update(job.get("crew_list") or [])

    earliest: Optional[Dict[str, Any]] = None
    latest: Optional[Dict[str, Any]] = None
    if jobs:
        earliest = min(jobs, key=lambda job: job["start_minutes"])
        latest = max(jobs, key=lambda job: job["finish_minutes"])

    return {
        "date_iso": date_iso,
        "date_heading": _date_heading(date_iso),
        "jobs": jobs,
        "summary": {
            "total_jobs": len(jobs),
            "crews": sorted(crews),
            "crew_display": " / ".join(sorted(crews)) if crews else "—",
            "earliest_start": earliest["start_display"] if earliest else "—",
            "latest_finish": latest["finish_display"] if latest else "—",
        },
    }
