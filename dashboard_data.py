"""Summary stats for the staff dashboard."""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import config
import database as db
import invoice
import wg_jobs

DASHBOARD_JOBS_INITIAL = 40
DASHBOARD_JOBS_PAGE_SIZE = 40
UPCOMING_DIVIDER_LABEL = "TODAY & UPCOMING"
PAYMENT_UNPAID_DIVIDER_LABEL = "REGULAR UNPAID"
PAYMENT_WG_DIVIDER_LABEL = wg_jobs.WG_SECTION_LABEL
PAYMENT_PAID_DIVIDER_LABEL = "PAID"


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


SECTION_TODAY_UPCOMING = 0
SECTION_UNPAID = 1
SECTION_WG_UNPAID = 2
SECTION_PAID = 3


def is_dashboard_paid(job: Any) -> bool:
    """True when the booking Payment status is Paid (not Unpaid / Part Paid / Overdue)."""
    raw = None
    if hasattr(job, "keys"):
        try:
            raw = job["payment_status"]
        except (KeyError, TypeError):
            raw = None
    elif isinstance(job, dict):
        raw = job.get("payment_status")
    return invoice.normalize_payment_status(raw) == invoice.PAYMENT_STATUS_PAID


def dashboard_section_group(job: Any, today_iso: str) -> int:
    """
    Independent Dashboard groups (each job belongs to exactly one):

    0. TODAY & UPCOMING — not Paid, job date today or later
    1. REGULAR UNPAID — not Paid, job date yesterday or earlier (Unpaid / Part Paid / Overdue)
    2. WG OUTDOOR LIFE — same as 1 but a WG contract job (paid in batches, never hidden by age)
    3. PAID — Payment status Paid, any date

    Must match database._dashboard_section_group (the SQL list sort order).
    """
    if is_dashboard_paid(job):
        return SECTION_PAID
    boundary = (today_iso or "")[:10]
    if boundary and job_move_date_iso(job) >= boundary:
        return SECTION_TODAY_UPCOMING
    if wg_jobs.is_wg_outdoor_life(job):
        return SECTION_WG_UNPAID
    return SECTION_UNPAID


def upcoming_divider_index(jobs: List[Any], today_iso: str) -> Optional[int]:
    """
    Index of the first visible unpaid job dated today or later (Perth today).

    Paid jobs are ignored. Returns None when that group is absent.
    """
    upcoming, _, _ = dashboard_section_indexes(jobs, today_iso)
    return upcoming


def dashboard_section_indexes(
    jobs: List[Any], today_iso: str
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Indexes of the first TODAY & UPCOMING, past REGULAR UNPAID, and PAID rows.

    None when that group is not present in the visible list.
    """
    layout = dashboard_section_layout(jobs, today_iso)
    return layout["upcoming"], layout["unpaid"], layout["paid"]


def dashboard_section_layout(jobs: List[Any], today_iso: str) -> Dict[str, Optional[int]]:
    """
    Row indexes where each Dashboard divider goes (None when absent).

    wg_last is the last visible WG row, so the template can close the WG block.
    """
    layout: Dict[str, Optional[int]] = {
        "upcoming": None,
        "unpaid": None,
        "wg": None,
        "wg_last": None,
        "paid": None,
    }
    keys = {
        SECTION_TODAY_UPCOMING: "upcoming",
        SECTION_UNPAID: "unpaid",
        SECTION_WG_UNPAID: "wg",
        SECTION_PAID: "paid",
    }
    for index, job in enumerate(jobs):
        group = dashboard_section_group(job, today_iso)
        key = keys[group]
        if layout[key] is None:
            layout[key] = index
        if group == SECTION_WG_UNPAID:
            layout["wg_last"] = index
    return layout


def dashboard_unpaid_summaries(jobs: List[Any], today_iso: str) -> Dict[str, Dict[str, Any]]:
    """
    Job count and invoice total for REGULAR UNPAID and WG OUTDOOR LIFE.

    Counts every job in that group (not just the visible page), so the
    totals stay right when the list is paginated.
    """
    groups = {SECTION_UNPAID: [], SECTION_WG_UNPAID: []}
    for job in jobs:
        group = dashboard_section_group(job, today_iso)
        if group in groups:
            groups[group].append(dict(job))
    rows = groups[SECTION_UNPAID] + groups[SECTION_WG_UNPAID]
    if rows:
        db.attach_extra_charges(rows)

    def _summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = sum(
            float(invoice.calculate_invoice_totals(item)["total"]) for item in items
        )
        return {"count": len(items), "total": round(total, 2)}

    return {
        "regular": _summary(groups[SECTION_UNPAID]),
        "wg": _summary(groups[SECTION_WG_UNPAID]),
    }


def week_range(today: date) -> tuple:
    """Monday–Sunday (ISO week) containing today."""
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _sales_summary(today: date) -> Dict[str, Any]:
    import sales_dashboard

    return sales_dashboard.build_sales_summary(today)


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
        "payment_mismatches": db.list_bank_transactions(
            match_status="mismatch", limit=20
        ),
        "sales": _sales_summary(today),
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
