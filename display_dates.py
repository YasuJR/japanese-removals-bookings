"""Format move dates for booking tables (display only; DB stays ISO)."""

from datetime import date, datetime, timedelta
from typing import Any, Dict, Tuple

_WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONDAY_WEEKDAY_LABELS_SHORT = _WEEKDAY_SHORT
MONDAY_WEEKDAY_LABELS_LONG = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTH_SHORT = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _parse_iso_date(date_string: Any):
    if isinstance(date_string, datetime):
        return date_string.date()
    if isinstance(date_string, date):
        return date_string
    text = str(date_string or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def monday_week_grid_bounds(first: date, last: date) -> Tuple[date, date]:
    """Inclusive calendar grid range with Monday-first weeks."""
    grid_start = first - timedelta(days=first.weekday())
    grid_end = last + timedelta(days=(6 - last.weekday()))
    return grid_start, grid_end


def monday_week_bounds(anchor: date) -> Tuple[date, date]:
    """Monday–Sunday week containing anchor."""
    start = anchor - timedelta(days=anchor.weekday())
    return start, start + timedelta(days=6)


def normalize_move_date(value: Any) -> str:
    """Return move_date as YYYY-MM-DD for queries and calendar grouping."""
    parsed = _parse_iso_date(value)
    return parsed.isoformat() if parsed else ""


def format_display_date(date_string: Any) -> Dict[str, str]:
    """
    Two-line display parts for booking tables.
    Example: {'weekday': 'Sun', 'day_month': '7 Jun 2026'}
    """
    parsed = _parse_iso_date(date_string)
    if parsed is None:
        fallback = str(date_string or "").strip() or "—"
        return {"weekday": "—", "day_month": fallback}
    return {
        "weekday": _WEEKDAY_SHORT[parsed.weekday()],
        "day_month": "{0} {1} {2}".format(
            parsed.day, _MONTH_SHORT[parsed.month - 1], parsed.year
        ),
    }


def get_weekday_class(date_string: Any) -> str:
    """
    CSS class for colour coding: weekday (navy), saturday (blue), sunday (red).
    """
    parsed = _parse_iso_date(date_string)
    if parsed is None:
        return "weekday"
    if parsed.weekday() == 5:
        return "saturday"
    if parsed.weekday() == 6:
        return "sunday"
    return "weekday"
