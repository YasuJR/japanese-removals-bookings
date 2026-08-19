"""Summary stats for the staff dashboard."""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import config
import database as db

DASHBOARD_JOBS_INITIAL = 40
DASHBOARD_JOBS_PAGE_SIZE = 40
UPCOMING_DIVIDER_LABEL = "TODAY & UPCOMING"


def perth_today(now: Optional[datetime] = None) -> date:
    """Current calendar date in Australia/Perth (config.TIMEZONE)."""
    tz = ZoneInfo(config.TIMEZONE)
    if now is None:
        return datetime.now(tz).date()
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    return now.astimezone(tz).date()


def job_move_date_iso(job: Any) -> str:
    """Normalize a booking move_date to YYYY-MM-DD."""
    raw = None
    if hasattr(job, "keys"):
        try:
            raw = job["move_date"]
        except (KeyError, TypeError):
            raw = None
    elif isinstance(job, dict):
        raw = job.get("move_date")
    if raw is None:
        return ""
    if hasattr(raw, "isoformat"):
        return str(raw.isoformat())[:10]
    return str(raw).strip()[:10]


def upcoming_divider_index(jobs: List[Any], today_iso: str) -> Optional[int]:
    """
    Index of the first visible job dated today or later (Perth today).

    Does not reorder jobs. Returns None when every visible row is in the past.
    """
    boundary = (today_iso or "")[:10]
    if not boundary:
        return None
    for index, job in enumerate(jobs):
        if job_move_date_iso(job) >= boundary:
            return index
    return None


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


def parse_jobs_limit(raw_limit: Any, jobs_total: int) -> int:
    """Return how many dashboard job rows to render (default 40, capped at total)."""
    if jobs_total <= 0:
        return 0
    default = min(DASHBOARD_JOBS_INITIAL, jobs_total)
    text = str(raw_limit or "").strip()
    if not text:
        return default
    try:
        limit = int(text)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, jobs_total))


def paginate_dashboard_jobs(
    jobs: List[Any],
    jobs_limit: int,
) -> Tuple[List[Any], int, bool, int]:
    """
    Slice sorted dashboard jobs for HTML rendering.

    Returns (visible_jobs, jobs_total, has_more, next_jobs_limit).
    """
    jobs_total = len(jobs)
    if jobs_total == 0:
        return [], 0, False, 0
    limit = max(1, min(jobs_limit, jobs_total))
    visible = jobs[:limit]
    has_more = limit < jobs_total
    next_limit = min(limit + DASHBOARD_JOBS_PAGE_SIZE, jobs_total)
    return visible, jobs_total, has_more, next_limit
