"""Staff Portal — one crew member's jobs for Today / Tomorrow / This Week.

Reads the existing bookings table only. Serializes operational fields and
never copies pricing, invoice, cost, or profit data into the page payload.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple
import re

import database as db
import job_status
from booking_helpers import apple_maps_url, pickup_suburb, sms_href, tel_href
from booking_times import (
    display_start_time,
    effective_start_hm,
    normalize_time_input,
)
from crew import CREW_OPTIONS, active_crew_names, crew_from_storage
from dashboard_data import perth_today, week_range
from display_dates import format_display_date, normalize_move_date
import staff_job_times

RANGE_TODAY = "today"
RANGE_TOMORROW = "tomorrow"
RANGE_WEEK = "week"

RANGE_TABS: List[Tuple[str, str]] = [
    (RANGE_TODAY, "Today"),
    (RANGE_TOMORROW, "Tomorrow"),
    (RANGE_WEEK, "This Week"),
]


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
    status = job_status.display(booking)
    if status == "Cancelled":
        return True
    # Today shows Completed jobs with a COMPLETED label. Tomorrow keeps
    # hiding them so that tab stays a forward-looking list.
    if status == "Completed" and range_key == RANGE_TOMORROW:
        return True
    return False


def _suburb_label(address: str) -> str:
    if not address:
        return ""
    label = pickup_suburb(address)
    if not label or label == "—":
        return address
    cleaned = re.sub(r"\s*W\.?A\.?\s*$", "", label, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\d{4}\s*$", "", cleaned).strip()
    cleaned = re.sub(r"\s*W\.?A\.?\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or label


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
    pickup_label = _suburb_label(pickup) if pickup else ""
    dropoff_label = _suburb_label(dropoff) if dropoff else ""
    times = staff_job_times.job_time_state(row)
    owner_start_hm = normalize_time_input(row.get("start_time"))
    owner_finish_hm = normalize_time_input(row.get("finish_time"))
    recorded = staff_job_times.recorded_actual_minutes(row)
    actual_minutes = staff_job_times.worked_minutes(row)
    if recorded is not None:
        actual_worked_display = (
            staff_job_times.format_worked_duration(recorded) or "0min"
        )
    elif actual_minutes > 0:
        actual_worked_display = staff_job_times.format_worked_duration(actual_minutes)
    else:
        actual_worked_display = "Not set"
    estimated_minutes = staff_job_times.duration_hours_to_minutes(
        row.get("duration_hours")
    )
    estimated_duration = staff_job_times.format_hours_as_worked(
        row.get("duration_hours")
    ) or "—"
    payload = {
        "id": int(row["id"]),
        "date_iso": move_date,
        "date_display": _date_display(move_date) if move_date else "—",
        "start_time": display_start_time(row),
        "start_hm": start_hm,
        "owner_start_hm": owner_start_hm,
        "owner_finish_hm": owner_finish_hm,
        "customer_name": str(row.get("customer_name") or "").strip() or "—",
        "pickup_address": pickup,
        "pickup_label": pickup_label or pickup,
        "dropoff_address": dropoff,
        "dropoff_label": dropoff_label or dropoff,
        "crew": _crew_slash_display(row),
        "estimated_duration": estimated_duration,
        "estimated_minutes": estimated_minutes,
        "actual_worked_display": actual_worked_display,
        "has_actual_worked": actual_worked_display != "Not set",
        "phone": phone,
        "notes": _notes_text(row),
        "tel_href": tel_href(phone),
        "sms_href": sms_href(phone),
        "pickup_map_url": apple_maps_url(pickup),
        "dropoff_map_url": apple_maps_url(dropoff),
    }
    payload.update(times)
    if payload.get("is_completed_status"):
        payload["status_display"] = "COMPLETED"
    return payload


def _week_days(
    jobs: List[Dict[str, Any]], start_iso: str, end_iso: str, today: date
) -> List[Dict[str, Any]]:
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for job in jobs:
        by_date.setdefault(job.get("date_iso") or "", []).append(job)
    try:
        start = date.fromisoformat(start_iso)
        end = date.fromisoformat(end_iso)
    except ValueError:
        return []
    days: List[Dict[str, Any]] = []
    current = start
    today_iso = today.isoformat()
    weekdays = (
        "MONDAY",
        "TUESDAY",
        "WEDNESDAY",
        "THURSDAY",
        "FRIDAY",
        "SATURDAY",
        "SUNDAY",
    )
    while current <= end:
        iso = current.isoformat()
        day_jobs = by_date.get(iso, [])
        day_minutes = staff_job_times.sum_worked_minutes(day_jobs, today)
        days.append(
            {
                "date_iso": iso,
                "heading": weekdays[current.weekday()],
                "date_display": _date_display(iso),
                "is_today": iso == today_iso,
                "jobs": day_jobs,
                "worked_minutes": day_minutes,
                "worked_display": staff_job_times.format_weekly_worked(day_minutes),
            }
        )
        current += timedelta(days=1)
    return days


def _booking_date_iso(booking: Dict[str, Any]) -> str:
    return normalize_move_date(booking.get("move_date")) or str(
        booking.get("move_date") or ""
    ).strip()[:10]


def _load_rows(start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
    """Same date query as This Week so Today does not miss jobs This Week shows."""
    rows = [dict(row) for row in db.list_between_dates(start_iso, end_iso)]
    matched: List[Dict[str, Any]] = []
    for row in rows:
        move_iso = _booking_date_iso(row)
        if move_iso and start_iso <= move_iso <= end_iso:
            matched.append(row)
    return matched


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

    week_days = (
        _week_days(jobs, start_iso, end_iso, today)
        if active_range == RANGE_WEEK
        else []
    )
    weekly_minutes = staff_job_times.sum_worked_minutes(jobs, today)
    weekly_estimated_minutes = staff_job_times.sum_estimated_minutes(jobs)
    weekly_worked = None
    if active_range == RANGE_WEEK:
        weekly_worked = {
            "staff": staff,
            "week_start": start_iso,
            "week_end": end_iso,
            "minutes": weekly_minutes,
            "display": staff_job_times.format_weekly_worked(weekly_minutes),
            "estimated_minutes": weekly_estimated_minutes,
            "estimated_display": staff_job_times.format_weekly_worked(
                weekly_estimated_minutes
            ),
        }

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
        "week_days": week_days,
        "weekly_worked": weekly_worked,
    }
