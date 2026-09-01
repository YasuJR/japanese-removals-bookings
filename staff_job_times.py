"""Actual work times — admin-entered, separate from scheduled/invoice times.

Staff Portal WEEKLY ACTUAL uses Owner Edit Booking start_time / finish_time
for jobs on or before today that have both times set. Future jobs are
excluded. WEEKLY ESTIMATED uses stored duration_hours for the whole week.
WEEKLY ESTIMATED uses stored duration_hours only.
Staff Portal is read-only.
"""

import re
from datetime import date, datetime, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

import job_status
from booking_times import format_time_12h, normalize_time_input
from display_dates import normalize_move_date

_CLOCK_12H = re.compile(
    r"(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp]\.?[Mm]\.?)"
)


def _from_12h_clock(text: str) -> str:
    match = _CLOCK_12H.search(str(text or "").strip())
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2))
    suffix = match.group(3).upper().replace(".", "")
    if hour < 1 or hour > 12 or minute > 59:
        return ""
    if suffix == "AM":
        hour = 0 if hour == 12 else hour
    else:
        hour = hour if hour == 12 else hour + 12
    return "{0:02d}:{1:02d}".format(hour, minute)


def parse_actual_clock(value: Any) -> str:
    """Normalize stored actual times to HH:MM.

    Accepts HH:MM, ISO datetimes, datetime/time values, and 12-hour
    strings such as 8:00 AM / 10:30 AM.
    """
    if isinstance(value, datetime):
        return "{0:02d}:{1:02d}".format(value.hour, value.minute)
    if isinstance(value, dt_time):
        return "{0:02d}:{1:02d}".format(value.hour, value.minute)
    text = str(value or "").strip()
    if not text:
        return ""
    from_12h = _from_12h_clock(text)
    if from_12h:
        return from_12h
    return normalize_time_input(text)


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


def duration_hours_to_minutes(hours: Any) -> int:
    """Stored duration_hours to minutes. Does not use start/finish times."""
    if isinstance(hours, bool) or hours is None:
        return 0
    if isinstance(hours, (int, float)):
        value = float(hours)
    else:
        text = str(hours).strip()
        if not text:
            return 0
        text = re.sub(r"(?i)\s*h(?:ou)?rs?\s*$", "", text).strip()
        try:
            value = float(text)
        except (TypeError, ValueError):
            return 0
    if value <= 0:
        return 0
    minutes = int(round(value * 60.0))
    return minutes if minutes > 0 else 0


def format_hours_as_worked(hours: Any) -> str:
    """Convert decimal hours such as 2.5 / 5.75 / 4.0 / 2.5hr to 2hr 30min form."""
    minutes = duration_hours_to_minutes(hours)
    if minutes <= 0:
        return ""
    return format_worked_duration(minutes)


def format_weekly_worked(minutes: Any) -> str:
    """Hours and minutes for weekly/day totals. Never decimal hours."""
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return "0hr"
    return format_worked_duration(total)


def parse_actual_duration_minutes(value: Any) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    return minutes


def booking_move_date(booking: Dict[str, Any]) -> Optional[date]:
    iso = normalize_move_date(
        booking.get("date_iso") or booking.get("move_date")
    )
    if not iso:
        return None
    try:
        return date.fromisoformat(iso)
    except ValueError:
        return None


def recorded_actual_minutes(booking: Dict[str, Any]) -> Optional[int]:
    """Minutes from actual_start_time / actual_finish_time when both are set.

    Returns 0 when both times exist but finish is not after start (e.g. 9:18 PM
    – 9:18 PM). Returns None when either actual time is missing so callers can
    fall back to Owner booking start/finish. Never uses duration_hours.
    """
    start = parse_actual_clock(booking.get("actual_start_time"))
    finish = parse_actual_clock(booking.get("actual_finish_time"))
    if not start or not finish:
        return None
    return duration_minutes_between(start, finish)


def worked_minutes(booking: Dict[str, Any], today: Optional[date] = None) -> int:
    """Worked minutes for Staff Portal Actual Worked / WEEKLY ACTUAL.

    Prefers Owner/Admin Actual Start / Finish when both are stored. Otherwise
    uses Edit Booking start_time / finish_time. Does not use duration_hours
    or 08:00/18:00 defaults. Cancelled jobs, future jobs (move_date after
    today), and jobs missing a usable start or finish are 0. Does not split
    by crew size.
    """
    if job_status.display(booking) == "Cancelled":
        return 0
    if today is not None:
        move = booking_move_date(booking)
        if move is not None and move > today:
            return 0
    recorded = recorded_actual_minutes(booking)
    if recorded is not None:
        return recorded
    start = parse_actual_clock(
        booking.get("owner_start_hm")
        or booking.get("start_time")
    )
    finish = parse_actual_clock(
        booking.get("owner_finish_hm")
        or booking.get("finish_time")
    )
    if not start or not finish:
        return 0
    return duration_minutes_between(start, finish)


def sum_worked_minutes(
    jobs: List[Dict[str, Any]], today: Optional[date] = None
) -> int:
    return sum(worked_minutes(job, today) for job in jobs)


def estimated_minutes(booking: Dict[str, Any]) -> int:
    """Minutes from stored duration_hours. Never start/finish or actual times."""
    if job_status.display(booking) == "Cancelled":
        return 0
    stored = booking.get("estimated_minutes")
    if stored not in (None, ""):
        try:
            minutes = int(stored)
            return minutes if minutes > 0 else 0
        except (TypeError, ValueError):
            pass
    return duration_hours_to_minutes(booking.get("duration_hours"))


def sum_estimated_minutes(jobs: List[Dict[str, Any]]) -> int:
    return sum(estimated_minutes(job) for job in jobs)


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


def format_hours_short(hours: Any) -> str:
    """Display hours as 2hr / 2.5hr / 2.75hr. Empty when missing or invalid."""
    if hours is None or hours == "":
        return ""
    try:
        value = float(hours)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    rounded = round(value, 2)
    if abs(rounded - int(rounded)) < 1e-9:
        text = str(int(rounded))
    else:
        text = "{0:.2f}".format(rounded).rstrip("0").rstrip(".")
    return "{0}hr".format(text)


def scheduled_hours(booking: Dict[str, Any]) -> Optional[float]:
    """Planned hours from stored start/finish. Falls back to duration_hours only."""
    from booking_times import duration_hours_from_times, parse_duration_hours

    start = parse_actual_clock(
        booking.get("owner_start_hm") or booking.get("start_time")
    )
    finish = parse_actual_clock(
        booking.get("owner_finish_hm") or booking.get("finish_time")
    )
    hours = duration_hours_from_times(start, finish)
    if hours is not None:
        return hours
    stored = parse_duration_hours(booking.get("duration_hours"))
    if stored is not None:
        return round(float(stored), 2)
    return None


def actual_hours(booking: Dict[str, Any]) -> Optional[float]:
    """Recorded actual start/finish, else stored actual_duration minutes.

    Does not use scheduled start/finish or duration_hours.
    """
    recorded = recorded_actual_minutes(booking)
    if recorded is not None:
        return round(recorded / 60.0, 2)
    duration = parse_actual_duration_minutes(booking.get("actual_duration"))
    if duration is not None:
        return round(duration / 60.0, 2)
    return None


def callout_hours(booking: Dict[str, Any]) -> Optional[float]:
    """Call-out hours from stored callout_fee / hourly_rate. No invoice change."""
    try:
        fee = float(booking.get("callout_fee") or 0)
        rate = float(booking.get("hourly_rate") or 0)
    except (TypeError, ValueError):
        return None
    if fee <= 0 or rate <= 0:
        return None
    hours = round(fee / rate, 2)
    if hours <= 0:
        return None
    return hours


def paid_hours(booking: Dict[str, Any]) -> Optional[float]:
    """Actual Hours + Call Out. None when actual time is not recorded."""
    actual = actual_hours(booking)
    if actual is None:
        return None
    extra = callout_hours(booking) or 0.0
    return round(actual + extra, 2)


def hours_or_zero(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def job_time_state(booking: Dict[str, Any]) -> Dict[str, Any]:
    start_raw = str(booking.get("actual_start_time") or "").strip()
    finish_raw = str(booking.get("actual_finish_time") or "").strip()
    start_hm = parse_actual_clock(start_raw)
    finish_hm = parse_actual_clock(finish_raw)
    started = bool(start_hm)
    finished = bool(finish_hm)
    duration = booking.get("actual_duration")
    computed = duration_minutes_between(start_hm, finish_hm) if started and finished else 0
    if computed:
        duration = computed
    worked = format_worked_duration(duration) if duration not in (None, "") else ""
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
