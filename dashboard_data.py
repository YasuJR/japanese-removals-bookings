"""Summary stats for the staff dashboard."""

from datetime import date, timedelta
from typing import Any, Dict, List

import database as db


def week_range(today: date) -> tuple:
    """Monday–Sunday (ISO week) containing today."""
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def build_dashboard(today: date = None) -> Dict[str, Any]:
    if today is None:
        today = date.today()
    tomorrow = today + timedelta(days=1)
    week_start, week_end = week_range(today)

    today_iso = today.isoformat()
    tomorrow_iso = tomorrow.isoformat()
    week_start_iso = week_start.isoformat()
    week_end_iso = week_end.isoformat()

    return {
        "today": today_iso,
        "tomorrow": tomorrow_iso,
        "week_start": week_start_iso,
        "week_end": week_end_iso,
        "today_jobs": db.list_by_date(today_iso),
        "tomorrow_jobs": db.list_by_date(tomorrow_iso),
        "week_jobs": db.list_between_dates(week_start_iso, week_end_iso),
        "total_movers_week": db.sum_movers_between_dates(
            week_start_iso, week_end_iso
        ),
        "upcoming_jobs": db.list_upcoming(today_iso),
    }


def dashboard_jobs(filter_name: str, today: date = None) -> list:
    if today is None:
        today = date.today()
    return db.list_for_dashboard(filter_name, today.isoformat())
