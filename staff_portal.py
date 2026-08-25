"""Staff Portal — one crew member's jobs for Today / Tomorrow / This Week.

Reads the existing bookings table only. Serializes operational fields and
never copies pricing, invoice, cost, or profit data into the page payload.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import database as db
import job_status
from booking_helpers import apple_maps_url, pickup_suburb, sms_href, tel_href
from booking_times import display_start_time, duration_hours_from_times, effective_finish_hm, effective_start_hm
from crew import CREW_OPTIONS, active_crew_names, crew_from_storage
from daily_jobs_data import format_job_duration_label
from dashboard_data import perth_today, week_range
from display_dates import format_display_date, normalize_move_date

RANGE_TODAY = "today"
RANGE_TOMORROW = "tomorrow"
RANGE_WEEK = "week"

RANGE_TABS: List[Tuple[str, str]] = [
    (RANGE_TODAY, "Today"),
    (RANGE_TOMORROW, "Tomorrow"),
    (RANGE_WEEK, "This Week"),
]

HIDDEN_STATUSES = frozenset({"Completed", "Cancelled"})


def normalize_range(value: Any) -> str:
    key = str(value or "").strip().lower()
    if key in {RANGE_TODAY, RANGE_TOMORROW, RANGE_WEEK}:
        return key
    return RANGE_TODAY


def _crew_options() -> List[str]:
    return list(active_crew_names() or CREW_OPTIONS)


def resolve_staff_name(staff_name: Any, options: Optional[Sequence[str]] = None) -> str:
    names = list(options) if options is not None else _crew_options()
    if not names:
        return ""
    requested = str(staff_name or "").strip()
    if requested in names:
        return requested
    return names[0]


def _range_dates(range_key: str, today: date) -> Tuple[str, str]:
    if range_key == RANGE_TOMORROW:
        day = (today + timedelta(days=1)).isoformat()
        return day, day
    if range_key == RANGE_WEEK:
        monday, sunday = week_range(today)
        return monday.isoformat(), sunday.isoformat()
    day = today.isoformat()
    return day, day


def _date_display(move_date: str) -> str:
    parts = format_display_date(move_date)
    return "{0} {1}".format(parts["weekday"], parts["day_month"])


def _crew_slash_display(booking: Dict[str, Any]) -> str:
    names = crew_from_storage(booking.get("crew"))
    return " / ".join(names) if names else "—"


def _notes_text(booking: Dict[str, Any]) -> str:
    text = str(booking.get("notes") or "").strip()
    if not text or text in ("—", "-", "–"):
        return ""
    if text.lower() in ("not provided", "n/a", "na", "none"):
        return ""
    return text


def _should_hide_status(booking: Dict[str, Any], range_key: str) -> bool:
    if job_status.display(booking) not in HIDDEN_STATUSES:
        return False
    return range_key in {RANGE_TODAY, RANGE_TOMORROW, RANGE_WEEK}


def _serialize_job(booking: Dict[str, Any]) -> Dict[str, Any]:
    """Operational fields only — no rates, invoices, costs, or profit."""
    row = dict(booking)
    move_date = normalize_move_date(row.get("move_date")) or str(
        row.get("move_date") or ""
    ).strip()[:10]
    pickup = str(row.get("pickup_address") or "").strip()
    dropoff = str(row.get("delivery_address") or "").strip()
    phone = str(row.get("phone") or "").strip()
    start_hm = effective_start_hm(row)
    finish_hm = effective_finish_hm(row)
    duration_label = format_job_duration_label(
        duration_hours_from_times(start_hm, finish_hm)
    )
    pickup_label = pickup_suburb(pickup) if pickup else ""
    dropoff_label = pickup_suburb(dropoff) if dropoff else ""
    return {
        "date_iso": move_date,
        "date_display": _date_display(move_date) if move_date else "—",
        "start_time": display_start_time(row),
        "start_hm": start_hm,
        "customer_name": str(row.get("customer_name") or "").strip() or "—",
        "pickup_address": pickup,
        "pickup_label": pickup_label if pickup_label != "—" else pickup,
        "dropoff_address": dropoff,
        "dropoff_label": dropoff_label if dropoff_label != "—" else dropoff,
        "crew": _crew_slash_display(row),
        "estimated_duration": duration_label or "—",
        "phone": phone,
        "notes": _notes_text(row),
        "tel_href": tel_href(phone),
        "sms_href": sms_href(phone),
        "pickup_map_url": apple_maps_url(pickup),
        "dropoff_map_url": apple_maps_url(dropoff),
    }


def _load_rows(start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
    if start_iso == end_iso:
        rows = db.list_by_date(start_iso)
    else:
        rows = db.list_between_dates(start_iso, end_iso)
    return [dict(row) for row in rows]


def build_staff_portal(
    staff_name: str = "",
    range_key: str = RANGE_TODAY,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    if today is None:
        today = perth_today()

    crew_names = _crew_options()
    staff = resolve_staff_name(staff_name, crew_names)
    active_range = normalize_range(range_key)
    start_iso, end_iso = _range_dates(active_range, today)

    jobs: List[Dict[str, Any]] = []
    if staff:
        for booking in _load_rows(start_iso, end_iso):
            if staff not in crew_from_storage(booking.get("crew")):
                continue
            if _should_hide_status(booking, active_range):
                continue
            jobs.append(_serialize_job(booking))

    jobs.sort(
        key=lambda job: (
            job.get("date_iso") or "",
            job.get("start_hm") or "",
            job.get("customer_name") or "",
        )
    )

    range_label = dict(RANGE_TABS).get(active_range, "Today")
    count = len(jobs)
    if active_range == RANGE_TODAY:
        jobs_label = "{0} Job{1} Today".format(count, "" if count == 1 else "s")
    elif active_range == RANGE_TOMORROW:
        jobs_label = "{0} Job{1} Tomorrow".format(count, "" if count == 1 else "s")
    else:
        jobs_label = "{0} Job{1} This Week".format(count, "" if count == 1 else "s")

    return {
        "staff": staff,
        "staff_options": crew_names,
        "range": active_range,
        "range_label": range_label,
        "range_tabs": RANGE_TABS,
        "start_date": start_iso,
        "end_date": end_iso,
        "jobs": jobs,
        "job_count": count,
        "jobs_label": jobs_label,
    }
