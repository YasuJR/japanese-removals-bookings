"""Actual start/finish times for Staff Portal jobs.

Stores Perth clock time on the server. Never overwrites booked start/finish
or estimated duration. actual_duration is minutes for later pay calculations.
"""

from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import config
import database as db
import job_status
from booking_times import format_time_12h
from crew import crew_from_storage


def perth_now(now: Optional[datetime] = None) -> datetime:
    tz = ZoneInfo(config.TIMEZONE)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def to_iso(moment: datetime) -> str:
    return perth_now(moment).isoformat(timespec="seconds")


def parse_actual_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return perth_now(parsed)


def format_actual_clock(value: Any) -> str:
    parsed = parse_actual_datetime(value)
    if parsed is None:
        return ""
    return format_time_12h("{0:02d}:{1:02d}".format(parsed.hour, parsed.minute))


def format_worked_duration(minutes: Any) -> str:
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return ""
    if total < 0:
        total = 0
    hours, mins = divmod(total, 60)
    if hours and mins:
        return "{0}hr {1}min".format(hours, mins)
    if hours:
        return "{0}hr".format(hours)
    return "{0}min".format(mins)


def duration_minutes_between(start_iso: Any, finish_iso: Any) -> int:
    start = parse_actual_datetime(start_iso)
    finish = parse_actual_datetime(finish_iso)
    if start is None or finish is None:
        return 0
    seconds = (finish - start).total_seconds()
    if seconds < 0:
        return 0
    return int(round(seconds / 60.0))


def _has_value(value: Any) -> bool:
    return bool(str(value or "").strip())


def job_time_state(booking: Dict[str, Any]) -> Dict[str, Any]:
    start_iso = str(booking.get("actual_start_time") or "").strip()
    finish_iso = str(booking.get("actual_finish_time") or "").strip()
    started = _has_value(start_iso)
    finished = _has_value(finish_iso)
    duration = booking.get("actual_duration")
    worked = format_worked_duration(duration) if duration is not None and str(duration) != "" else ""
    if finished and not worked:
        worked = format_worked_duration(duration_minutes_between(start_iso, finish_iso))
    status = job_status.display(booking)
    cancelled = status == "Cancelled"
    return {
        "actual_start_time": start_iso,
        "actual_finish_time": finish_iso,
        "actual_duration": duration,
        "started": started,
        "finished": finished,
        "started_display": format_actual_clock(start_iso) if started else "",
        "finished_display": format_actual_clock(finish_iso) if finished else "",
        "worked_display": worked,
        "can_start": (not started) and (not cancelled),
        "can_finish": started and (not finished) and (not cancelled),
        "is_completed_status": status == "Completed",
        "status_display": status if status == "Completed" else "",
    }


def staff_can_update_job(booking: Dict[str, Any], staff_name: str) -> bool:
    staff = str(staff_name or "").strip()
    if not staff:
        return False
    if job_status.display(booking) == "Cancelled":
        return False
    return staff in crew_from_storage(booking.get("crew"))


def start_job(
    booking_id: int,
    staff_name: str,
    now: Optional[datetime] = None,
) -> bool:
    row = db.get_booking(booking_id)
    if not row:
        return False
    booking = dict(row)
    if not staff_can_update_job(booking, staff_name):
        return False
    if job_time_state(booking)["started"]:
        return False
    return db.try_set_actual_start(booking_id, to_iso(perth_now(now)))


def finish_job(
    booking_id: int,
    staff_name: str,
    now: Optional[datetime] = None,
) -> bool:
    row = db.get_booking(booking_id)
    if not row:
        return False
    booking = dict(row)
    if not staff_can_update_job(booking, staff_name):
        return False
    state = job_time_state(booking)
    if not state["can_finish"]:
        return False
    finish_at = perth_now(now)
    minutes = duration_minutes_between(state["actual_start_time"], to_iso(finish_at))
    return db.try_set_actual_finish(booking_id, to_iso(finish_at), minutes)
