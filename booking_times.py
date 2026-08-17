"""Start/finish time helpers for bookings and Google Calendar."""

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple

from zoneinfo import ZoneInfo

import config

DEFAULT_START_TIME = "08:00"
DEFAULT_FINISH_TIME = "18:00"
DEFAULT_DURATION_HOURS = 10.0


def normalize_time_input(value: Any) -> str:
    """Return HH:MM or empty string from form input."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 5 and text[2] == ":":
        return text
    return ""


def parse_hhmm(value: str) -> Optional[time]:
    text = normalize_time_input(value)
    if not text:
        return None
    try:
        hour, minute = text.split(":")
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return None


def effective_start_hm(booking: Dict[str, Any]) -> str:
    stored = normalize_time_input(booking.get("start_time"))
    return stored or DEFAULT_START_TIME


def effective_finish_hm(booking: Dict[str, Any]) -> str:
    stored = normalize_time_input(booking.get("finish_time"))
    return stored or DEFAULT_FINISH_TIME


def finish_from_duration(start_hm: str, duration_hours: float) -> str:
    """Add duration to start time; returns HH:MM (same day, capped at 23:59)."""
    start_t = parse_hhmm(start_hm) or parse_hhmm(DEFAULT_START_TIME)
    if start_t is None:
        start_t = time(8, 0)
    base = datetime.combine(date.min, start_t)
    end = base + timedelta(hours=float(duration_hours))
    if end.date() > date.min:
        return "23:59"
    return end.strftime("%H:%M")


def parse_duration_hours(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        hours = float(text)
        if hours <= 0 or hours > 24:
            return None
        return hours
    except (TypeError, ValueError):
        return None


def inferred_duration_hours(booking: Dict[str, Any]) -> Optional[float]:
    """Hours between start and finish using effective times when needed."""
    return duration_hours_from_times(
        effective_start_hm(booking), effective_finish_hm(booking)
    )


def duration_hours_from_times(start_time: Any, finish_time: Any) -> Optional[float]:
    """Hours between explicit start/finish times (HH:MM). Source of truth for invoicing."""
    start_norm = normalize_time_input(start_time)
    finish_norm = normalize_time_input(finish_time)
    if not start_norm or not finish_norm:
        return None
    start_t = parse_hhmm(start_norm)
    finish_t = parse_hhmm(finish_norm)
    if not start_t or not finish_t:
        return None
    start_dt = datetime.combine(date.min, start_t)
    finish_dt = datetime.combine(date.min, finish_t)
    if finish_dt <= start_dt:
        return None
    delta = finish_dt - start_dt
    return round(delta.total_seconds() / 3600, 2)


def effective_duration_hours(booking: Dict[str, Any], *, default: float = 1.0) -> float:
    """
    Invoice duration: prefer explicit start/finish times, then stored duration.
    """
    from_times = duration_hours_from_times(
        booking.get("start_time"), booking.get("finish_time")
    )
    if from_times is not None:
        return from_times
    stored = parse_duration_hours(booking.get("duration_hours"))
    if stored is not None:
        return stored
    inferred = inferred_duration_hours(booking)
    if inferred is not None:
        return inferred
    return default


def format_time_12h(hm: str) -> str:
    """Format HH:MM as e.g. 8:00 AM."""
    parsed = parse_hhmm(hm)
    if parsed is None:
        return hm
    hour = parsed.hour % 12 or 12
    suffix = "AM" if parsed.hour < 12 else "PM"
    return "{0}:{1:02d} {2}".format(hour, parsed.minute, suffix)


def display_start_time(booking: Dict[str, Any]) -> str:
    return format_time_12h(effective_start_hm(booking))


def display_finish_time(booking: Dict[str, Any]) -> str:
    return format_time_12h(effective_finish_hm(booking))


def resolve_finish_time(
    start_time: str,
    finish_time: str,
    duration_hours: Optional[float],
) -> Tuple[str, str, str, list]:
    """
    Return (start_norm, finish_norm, duration_storage, errors).
    duration_storage is '' or string like '3' / '3.5'.
    When both duration and an explicit finish time are supplied, the finish
    time wins and duration is derived from start/finish.
    """
    errors = []
    start_norm = normalize_time_input(start_time)
    finish_norm = normalize_time_input(finish_time)

    if duration_hours is not None:
        base_start = start_norm or DEFAULT_START_TIME
        if not start_norm:
            start_norm = DEFAULT_START_TIME
        derived_finish = finish_from_duration(base_start, duration_hours)
        if finish_norm and finish_norm != derived_finish:
            start_t = parse_hhmm(start_norm)
            finish_t = parse_hhmm(finish_norm)
            if start_t and finish_t:
                if finish_t <= start_t:
                    errors.append("Finish time must be after start time.")
                    return start_norm, finish_norm, "", errors
                delta = datetime.combine(date.min, finish_t) - datetime.combine(
                    date.min, start_t
                )
                hours = round(delta.total_seconds() / 3600, 2)
                duration_storage = (
                    str(int(hours)) if hours == int(hours) else str(hours)
                )
                return start_norm, finish_norm, duration_storage, errors
        finish_norm = derived_finish
        duration_storage = (
            str(int(duration_hours))
            if duration_hours == int(duration_hours)
            else str(duration_hours)
        )
    else:
        duration_storage = ""
        if not finish_norm:
            finish_norm = DEFAULT_FINISH_TIME
        if not start_norm:
            start_norm = DEFAULT_START_TIME

    start_t = parse_hhmm(start_norm)
    finish_t = parse_hhmm(finish_norm)
    if start_t and finish_t and finish_t <= start_t:
        errors.append("Finish time must be after start time.")

    return start_norm, finish_norm, duration_storage, errors


def validate_times(
    start_time: str,
    finish_time: str,
    duration_hours_raw: Any = "",
) -> Tuple[str, str, str, list]:
    """Validate optional times; duration mode overrides finish time."""
    errors = []
    duration = parse_duration_hours(duration_hours_raw)
    if str(duration_hours_raw or "").strip() and duration is None:
        errors.append("Duration must be a number of hours between 0 and 24.")

    start_norm, finish_norm, duration_storage, time_errors = resolve_finish_time(
        start_time, finish_time, duration
    )
    errors.extend(time_errors)
    return start_norm, finish_norm, duration_storage, errors


def event_datetimes(booking: Dict[str, Any]) -> Tuple[datetime, datetime]:
    """Perth-local start/end datetimes for calendar sync."""
    tz = ZoneInfo(config.TIMEZONE)
    move_day = datetime.strptime(booking["move_date"], "%Y-%m-%d").date()
    start_t = parse_hhmm(effective_start_hm(booking))
    finish_t = parse_hhmm(effective_finish_hm(booking))
    if start_t is None:
        start_t = time(8, 0)
    if finish_t is None:
        finish_t = time(18, 0)
    return (
        datetime.combine(move_day, start_t, tzinfo=tz),
        datetime.combine(move_day, finish_t, tzinfo=tz),
    )
