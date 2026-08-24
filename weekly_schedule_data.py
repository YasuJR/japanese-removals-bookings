"""Weekly Schedule — read-only Mon–Sun listing of existing bookings."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from dashboard_data import perth_today
from daily_jobs_data import build_daily_jobs
from display_dates import normalize_move_date

_MONTHS = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)


def monday_of_week(value: Any) -> date:
    """Return the Monday of the week containing value (Mon–Sun)."""
    iso = normalize_move_date(value)
    if iso:
        parsed = datetime.strptime(iso, "%Y-%m-%d").date()
    else:
        parsed = perth_today()
    return parsed - timedelta(days=parsed.weekday())


def _day_heading(day: date) -> str:
    return "{0} {1} {2}".format(
        day.strftime("%A").upper(),
        day.day,
        _MONTHS[day.month - 1],
    )


def _week_range_heading(monday: date, sunday: date) -> str:
    start = "{0} {1}".format(monday.day, _MONTHS[monday.month - 1])
    end = "{0} {1}".format(sunday.day, _MONTHS[sunday.month - 1])
    if monday.year == sunday.year:
        return "{0} – {1} {2}".format(start, end, sunday.year)
    return "{0} {1} – {2} {3}".format(start, monday.year, end, sunday.year)


def build_weekly_schedule(
    anchor_iso: str,
    *,
    reference: Optional[date] = None,
) -> Dict[str, Any]:
    """Load existing bookings for the Mon–Sun week containing anchor_iso."""
    monday = monday_of_week(anchor_iso)
    sunday = monday + timedelta(days=6)
    today = reference or perth_today()
    this_monday = monday_of_week(today.isoformat())

    days: List[Dict[str, Any]] = []
    total_jobs = 0
    for offset in range(7):
        day = monday + timedelta(days=offset)
        iso = day.isoformat()
        daily = build_daily_jobs(iso)
        jobs = list(daily.get("jobs") or [])
        total_jobs += len(jobs)
        days.append(
            {
                "date_iso": iso,
                "heading": _day_heading(day),
                "weekday": day.strftime("%A").upper(),
                "is_today": day == today,
                "is_weekend": day.weekday() >= 5,
                "jobs": jobs,
                "is_empty": not jobs,
            }
        )

    return {
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "range_heading": _week_range_heading(monday, sunday),
        "prev_week": (monday - timedelta(days=7)).isoformat(),
        "next_week": (monday + timedelta(days=7)).isoformat(),
        "this_week": this_monday.isoformat(),
        "is_this_week": monday == this_monday,
        "days": days,
        "total_jobs": total_jobs,
    }
