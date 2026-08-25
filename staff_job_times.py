"""Actual work times — admin-entered, separate from scheduled/invoice times.

actual_duration is stored as minutes for later pay calculations.
Staff Portal is read-only.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import job_status
from booking_times import format_time_12h, normalize_time_input


def parse_actual_clock(value: Any) -> str:
    """Normalize stored actual times (HH:MM or ISO datetime) to HH:MM."""
    return normalize_time_input(value)


def format_actual_clock(value: Any) -> str:
    hm = parse_actual_clock(value)
    if not hm:
        return ""
    return format_time_12h(hm)


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


def duration_minutes_between(start_value: Any, finish_value: Any) -> int:
    start_hm = parse_actual_clock(start_value)
    finish_hm = parse_actual_clock(finish_value)
    if not start_hm or not finish_hm:
        return 0
    start = datetime.strptime(start_hm, "%H:%M")
    finish = datetime.strptime(finish_hm, "%H:%M")
    seconds = (finish - start).total_seconds()
    if seconds < 0:
        return 0
    return int(round(seconds / 60.0))


def parse_actual_times_from_form(form: Any) -> Tuple[str, str, Optional[int], List[str]]:
    """Read admin Actual Start / Finish. Does not touch scheduled times."""
    start = parse_actual_clock(form.get("actual_start_time") if hasattr(form, "get") else "")
    finish = parse_actual_clock(form.get("actual_finish_time") if hasattr(form, "get") else "")
    errors: List[str] = []
    duration: Optional[int] = None
    if finish and not start:
        errors.append("Actual start time is required when actual finish time is set.")
        return start, finish, duration, errors
    if start and finish:
        minutes = duration_minutes_between(start, finish)
        if minutes <= 0:
            errors.append("Actual finish time must be after actual start time.")
        else:
            duration = minutes
    return start, finish, duration, errors


def job_time_state(booking: Dict[str, Any]) -> Dict[str, Any]:
    start_raw = str(booking.get("actual_start_time") or "").strip()
    finish_raw = str(booking.get("actual_finish_time") or "").strip()
    start_hm = parse_actual_clock(start_raw)
    finish_hm = parse_actual_clock(finish_raw)
    started = bool(start_hm)
    finished = bool(finish_hm)
    duration = booking.get("actual_duration")
    worked = (
        format_worked_duration(duration)
        if duration is not None and str(duration) != ""
        else ""
    )
    if started and finished and not worked:
        worked = format_worked_duration(duration_minutes_between(start_hm, finish_hm))
    started_display = format_actual_clock(start_hm) if started else ""
    finished_display = format_actual_clock(finish_hm) if finished else ""
    actual_range = ""
    if started and finished:
        actual_range = "{0} – {1}".format(started_display, finished_display)
    elif started:
        actual_range = started_display
    status = job_status.display(booking)
    return {
        "actual_start_hm": start_hm,
        "actual_finish_hm": finish_hm,
        "actual_start_time": start_raw,
        "actual_finish_time": finish_raw,
        "actual_duration": duration,
        "has_actual": started or finished,
        "started_display": started_display,
        "finished_display": finished_display,
        "actual_range_display": actual_range,
        "worked_display": worked,
        "is_completed_status": status == "Completed",
        "status_display": status if status == "Completed" else "",
    }
