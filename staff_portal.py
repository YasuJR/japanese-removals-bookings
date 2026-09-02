"""Staff Portal — one crew member's jobs, weekly hours, and history.

Reads the existing bookings table only. Serializes operational fields and
never copies pricing, invoice, cost, or profit data into the page payload.
Staff identity comes from the login session, not URL parameters.
"""

from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple
import re

import database as db
import job_status
from booking_helpers import apple_maps_url, pickup_suburb, sms_href, tel_href
from booking_times import (
    display_start_time,
    effective_start_hm,
    format_time_12h,
    normalize_time_input,
)
from crew import CREW_OPTIONS, active_crew_names, all_crew_names, crew_from_storage
from dashboard_data import perth_today, week_range
from display_dates import format_display_date, normalize_move_date
import staff_job_times

RANGE_TODAY = "today"
RANGE_CALENDAR = "calendar"
RANGE_WEEK = "week"
RANGE_HISTORY = "history"

RANGE_TABS: List[Tuple[str, str]] = [
    (RANGE_TODAY, "Today"),
    (RANGE_CALENDAR, "Calendar"),
    (RANGE_WEEK, "Weekly"),
    (RANGE_HISTORY, "History"),
]

JOBS_LOOKBACK_DAYS = 400
JOBS_LOOKAHEAD_DAYS = 180
HISTORY_LOOKBACK_DAYS = 400
MAX_WEEK_OFFSET_PAST = 104
MAX_WEEK_OFFSET_FUTURE = 12
MAX_CALENDAR_MONTH_OFFSET = 24


def normalize_range(value: Any) -> str:
    key = str(value or "").strip().lower()
    if key in ("jobs", "tomorrow"):
        return RANGE_TODAY
    if key in {RANGE_TODAY, RANGE_CALENDAR, RANGE_WEEK, RANGE_HISTORY}:
        return key
    return RANGE_TODAY


def normalize_calendar_month(
    year: Any, month: Any, today: date
) -> Tuple[int, int]:
    try:
        y = int(year)
        m = int(month)
    except (TypeError, ValueError):
        return today.year, today.month
    if m < 1 or m > 12:
        return today.year, today.month
    if y < 2000 or y > 2100:
        return today.year, today.month
    return y, m


def normalize_calendar_day(
    day_iso: Any, year: int, month: int
) -> str:
    text = str(day_iso or "").strip()[:10]
    if not text:
        return ""
    try:
        picked = date.fromisoformat(text)
    except ValueError:
        return ""
    if picked.year != year or picked.month != month:
        return ""
    return picked.isoformat()


def _shift_month(year: int, month: int, delta: int) -> Tuple[int, int]:
    month += delta
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return year, month


def normalize_week_offset(value: Any) -> int:
    try:
        offset = int(value)
    except (TypeError, ValueError):
        return 0
    if offset < -MAX_WEEK_OFFSET_PAST:
        return -MAX_WEEK_OFFSET_PAST
    if offset > MAX_WEEK_OFFSET_FUTURE:
        return MAX_WEEK_OFFSET_FUTURE
    return offset


def _crew_options() -> List[str]:
    return list(active_crew_names() or CREW_OPTIONS)


def _known_staff_names() -> List[str]:
    names = list(all_crew_names() or [])
    for name in _crew_options():
        if name not in names:
            names.append(name)
    return names


def resolve_staff_name(staff_name: Any, options: Optional[Sequence[str]] = None) -> str:
    """Return the requested crew name only when it is a known staff member.

    Never falls back to another person.
    """
    names = list(options) if options is not None else _known_staff_names()
    requested = str(staff_name or "").strip()
    if requested and requested in names:
        return requested
    return ""


def resolve_staff_id(staff_id: Any) -> str:
    """Map a crew_members.id to that member's name. Empty when unknown."""
    raw = str(staff_id or "").strip()
    if not raw:
        return ""
    try:
        wanted = int(raw)
    except (TypeError, ValueError):
        return ""
    for member in db.list_crew_members(active_only=False):
        try:
            if int(member.get("id") or 0) == wanted:
                return str(member.get("name") or "").strip()
        except (TypeError, ValueError):
            continue
    return ""


def bound_staff_identity(
    staff_name: Any = "",
    staff_id: Any = None,
    options: Optional[Sequence[str]] = None,
) -> str:
    """Server-side staff identity. Name wins; staff ID is a fallback lookup."""
    names = list(options) if options is not None else _known_staff_names()
    from_name = resolve_staff_name(staff_name, names)
    if from_name:
        return from_name
    from_id = resolve_staff_id(staff_id)
    return resolve_staff_name(from_id, names)


def _range_dates(
    range_key: str, today: date, week_offset: int = 0, cal_year: int = 0, cal_month: int = 0
) -> Tuple[str, str]:
    if range_key == RANGE_TODAY:
        iso = today.isoformat()
        return iso, iso
    if range_key == RANGE_CALENDAR:
        year, month = cal_year, cal_month
        if not year or not month:
            year, month = today.year, today.month
        last_day = monthrange(year, month)[1]
        return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()
    if range_key == RANGE_WEEK:
        monday, sunday = week_range(today)
        monday = monday + timedelta(weeks=week_offset)
        sunday = sunday + timedelta(weeks=week_offset)
        return monday.isoformat(), sunday.isoformat()
    if range_key == RANGE_HISTORY:
        start = today - timedelta(days=HISTORY_LOOKBACK_DAYS)
        end = today - timedelta(days=1)
        return start.isoformat(), end.isoformat()
    start = today - timedelta(days=JOBS_LOOKBACK_DAYS)
    end = today + timedelta(days=JOBS_LOOKAHEAD_DAYS)
    return start.isoformat(), end.isoformat()


def _work_days_count(jobs: List[Dict[str, Any]]) -> int:
    dates = {job.get("date_iso") or "" for job in jobs}
    dates.discard("")
    return len(dates)


def _month_heading(year: int, month: int) -> str:
    anchor = date(year, month, 1)
    return anchor.strftime("%B %Y")


def _calendar_grid_bounds(year: int, month: int) -> Tuple[date, date, date, date]:
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    grid_start = first - timedelta(days=(first.weekday() + 1) % 7)
    grid_end = last + timedelta(days=(6 - ((last.weekday() + 1) % 7)))
    return grid_start, grid_end, first, last


def _build_staff_calendar(
    jobs: List[Dict[str, Any]],
    year: int,
    month: int,
    today: date,
    selected_day_iso: str = "",
) -> Dict[str, Any]:
    grid_start, grid_end, month_first, month_last = _calendar_grid_bounds(year, month)
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for job in jobs:
        iso = job.get("date_iso") or ""
        if grid_start.isoformat() <= iso <= grid_end.isoformat():
            by_date.setdefault(iso, []).append(job)
    for day_jobs in by_date.values():
        day_jobs.sort(
            key=lambda item: (
                item.get("start_hm") or "",
                item.get("customer_name") or "",
            )
        )

    cells: List[Dict[str, Any]] = []
    current = grid_start
    today_iso = today.isoformat()
    while current <= grid_end:
        iso = current.isoformat()
        in_month = month_first <= current <= month_last
        day_jobs = by_date.get(iso, []) if in_month else []
        cells.append(
            {
                "date_iso": iso,
                "day_num": current.day,
                "in_month": in_month,
                "is_today": iso == today_iso,
                "has_jobs": bool(day_jobs),
                "job_count": len(day_jobs),
                "selected": iso == selected_day_iso,
            }
        )
        current += timedelta(days=1)

    selected_jobs = by_date.get(selected_day_iso, []) if selected_day_iso else []
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)
    is_current_month = year == today.year and month == today.month

    return {
        "year": year,
        "month": month,
        "month_label": _month_heading(year, month),
        "weekday_labels": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        "cells": cells,
        "selected_date_iso": selected_day_iso,
        "selected_date_display": _date_display(selected_day_iso) if selected_day_iso else "",
        "selected_jobs": selected_jobs,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
        "is_current_month": is_current_month,
    }


def _date_display(move_date: str) -> str:
    parts = format_display_date(move_date)
    return "{0} {1}".format(parts["weekday"], parts["day_month"])


def _week_label(start_iso: str, end_iso: str) -> str:
    start_parts = format_display_date(start_iso)
    end_parts = format_display_date(end_iso)
    return "{0} – {1}".format(start_parts["day_month"], end_parts["day_month"])


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


def _should_hide_status(booking: Dict[str, Any]) -> bool:
    return job_status.display(booking) == "Cancelled"


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


def _scheduled_range_display(booking: Dict[str, Any]) -> str:
    start = normalize_time_input(booking.get("start_time"))
    finish = normalize_time_input(booking.get("finish_time"))
    if start and finish:
        return "{0} – {1}".format(format_time_12h(start), format_time_12h(finish))
    if start:
        return format_time_12h(start)
    return "—"


def _status_badge(booking: Dict[str, Any]) -> Tuple[str, bool]:
    status = job_status.display(booking)
    if status == "Completed":
        return "COMPLETED", True
    if status in ("Invoiced", "Paid", "On Route", "In Progress", "Confirmed"):
        return status.upper(), status in ("Invoiced", "Paid")
    return "", False


def _job_hours_payload(row: Dict[str, Any], today: date) -> Dict[str, Any]:
    scheduled = staff_job_times.scheduled_hours(row)
    actual = staff_job_times.actual_hours(row)
    callout = staff_job_times.callout_hours(row)
    paid = staff_job_times.paid_hours(row)
    move = staff_job_times.booking_move_date(row)
    is_future = move is not None and move > today
    if actual is None:
        actual_display = "Not completed" if is_future else "—"
        paid_display = "—"
        has_actual_hours = False
    else:
        actual_display = staff_job_times.format_hours_short(actual) or "0hr"
        paid_display = staff_job_times.format_hours_short(paid) or "0hr"
        has_actual_hours = True
    callout_label = ""
    if callout:
        callout_label = "+ {0} call out".format(
            staff_job_times.format_hours_short(callout)
        )
    return {
        "scheduled_hours": scheduled,
        "scheduled_hours_display": staff_job_times.format_hours_short(scheduled)
        or "—",
        "scheduled_range_display": _scheduled_range_display(row),
        "actual_hours": actual,
        "actual_hours_display": actual_display,
        "has_actual_hours": has_actual_hours,
        "callout_hours": callout,
        "callout_hours_label": callout_label,
        "paid_hours": paid,
        "paid_hours_display": paid_display,
    }


def _serialize_job(booking: Dict[str, Any], today: date) -> Dict[str, Any]:
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
    hours = _job_hours_payload(row, today)
    estimated_minutes = staff_job_times.duration_hours_to_minutes(
        row.get("duration_hours")
    )
    estimated_duration = staff_job_times.format_hours_as_worked(
        row.get("duration_hours")
    ) or "—"
    status_display, is_done_status = _status_badge(row)
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
        "actual_worked_display": hours["actual_hours_display"],
        "has_actual_worked": hours["has_actual_hours"],
        "phone": phone,
        "notes": _notes_text(row),
        "tel_href": tel_href(phone),
        "sms_href": sms_href(phone),
        "pickup_map_url": apple_maps_url(pickup),
        "dropoff_map_url": apple_maps_url(dropoff),
        "status_display": status_display,
        "is_completed_status": bool(times.get("is_completed_status")) or is_done_status,
    }
    payload.update(times)
    payload.update(hours)
    payload["status_display"] = status_display
    payload["is_completed_status"] = bool(times.get("is_completed_status")) or is_done_status
    return payload


def _hours_summary(jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    scheduled = 0.0
    actual = 0.0
    callout = 0.0
    for job in jobs:
        scheduled += staff_job_times.hours_or_zero(job.get("scheduled_hours"))
        actual += staff_job_times.hours_or_zero(job.get("actual_hours"))
        callout += staff_job_times.hours_or_zero(job.get("callout_hours"))
    scheduled = round(scheduled, 2)
    actual = round(actual, 2)
    callout = round(callout, 2)
    paid = round(actual + callout, 2)
    return {
        "job_count": len(jobs),
        "scheduled_hours": scheduled,
        "scheduled_display": staff_job_times.format_hours_short(scheduled) or "0hr",
        "actual_hours": actual,
        "actual_display": staff_job_times.format_hours_short(actual) or "0hr",
        "callout_hours": callout,
        "callout_display": staff_job_times.format_hours_short(callout) or "0hr",
        "paid_hours": paid,
        "paid_display": staff_job_times.format_hours_short(paid) or "0hr",
    }


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
        summary = _hours_summary(day_jobs)
        days.append(
            {
                "date_iso": iso,
                "heading": weekdays[current.weekday()],
                "date_display": _date_display(iso),
                "is_today": iso == today_iso,
                "jobs": day_jobs,
                "job_count": summary["job_count"],
                "scheduled_display": summary["scheduled_display"],
                "actual_display": summary["actual_display"],
                "callout_display": summary["callout_display"],
                "paid_display": summary["paid_display"],
                "worked_minutes": int(round(summary["paid_hours"] * 60)),
                "worked_display": summary["paid_display"],
            }
        )
        current += timedelta(days=1)
    return days


def _history_weeks(
    jobs: List[Dict[str, Any]], today: date
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for job in jobs:
        iso = job.get("date_iso") or ""
        try:
            move = date.fromisoformat(iso)
        except ValueError:
            continue
        monday, sunday = week_range(move)
        key = monday.isoformat()
        grouped.setdefault(key, []).append(job)
    weeks: List[Dict[str, Any]] = []
    for monday_iso in sorted(grouped.keys(), reverse=True):
        monday = date.fromisoformat(monday_iso)
        sunday = monday + timedelta(days=6)
        week_jobs = grouped[monday_iso]
        week_jobs.sort(
            key=lambda job: (
                job.get("date_iso") or "",
                job.get("start_hm") or "",
                job.get("customer_name") or "",
            ),
            reverse=True,
        )
        summary = _hours_summary(week_jobs)
        weeks.append(
            {
                "week_start": monday_iso,
                "week_end": sunday.isoformat(),
                "label": _week_label(monday_iso, sunday.isoformat()),
                "is_current": week_range(today)[0].isoformat() == monday_iso,
                "jobs": week_jobs,
                "job_count": summary["job_count"],
                "paid_hours": summary["paid_hours"],
                "paid_display": summary["paid_display"],
                "scheduled_display": summary["scheduled_display"],
                "actual_display": summary["actual_display"],
                "callout_display": summary["callout_display"],
            }
        )
    return weeks


def _booking_date_iso(booking: Dict[str, Any]) -> str:
    return normalize_move_date(booking.get("move_date")) or str(
        booking.get("move_date") or ""
    ).strip()[:10]


def _staff_assigned(booking: Dict[str, Any], staff: str) -> bool:
    if not staff:
        return False
    return staff in crew_from_storage(booking.get("crew"))


def _load_rows(staff: str, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
    """Server-side fetch: date window, then only the logged-in staff's crew jobs."""
    if not staff or not start_iso or not end_iso:
        return []
    rows = [dict(row) for row in db.list_between_dates(start_iso, end_iso)]
    matched: List[Dict[str, Any]] = []
    for row in rows:
        if not _staff_assigned(row, staff):
            continue
        if _should_hide_status(row):
            continue
        move_iso = _booking_date_iso(row)
        if move_iso and start_iso <= move_iso <= end_iso:
            matched.append(row)
    return matched


def _split_job_sections(
    jobs: List[Dict[str, Any]], today: date
) -> List[Dict[str, Any]]:
    today_iso = today.isoformat()
    future: List[Dict[str, Any]] = []
    today_jobs: List[Dict[str, Any]] = []
    past: List[Dict[str, Any]] = []
    for job in jobs:
        iso = job.get("date_iso") or ""
        if iso == today_iso:
            today_jobs.append(job)
        elif iso > today_iso:
            future.append(job)
        else:
            past.append(job)
    future.sort(
        key=lambda job: (
            job.get("date_iso") or "",
            job.get("start_hm") or "",
            job.get("customer_name") or "",
        )
    )
    today_jobs.sort(
        key=lambda job: (
            job.get("start_hm") or "",
            job.get("customer_name") or "",
        )
    )
    past.sort(
        key=lambda job: (
            job.get("date_iso") or "",
            job.get("start_hm") or "",
            job.get("customer_name") or "",
        ),
        reverse=True,
    )
    return [
        {"key": "future", "title": "Future", "jobs": future},
        {"key": "today", "title": "Today", "jobs": today_jobs},
        {"key": "past", "title": "Past", "jobs": past},
    ]


def build_staff_portal(
    staff_name: str = "",
    range_key: str = RANGE_TODAY,
    today: Optional[date] = None,
    week_offset: int = 0,
    staff_id: Any = None,
    calendar_year: Any = None,
    calendar_month: Any = None,
    calendar_day: Any = None,
) -> Dict[str, Any]:
    if today is None:
        today = perth_today()

    crew_names = _crew_options()
    staff = bound_staff_identity(staff_name, staff_id)
    active_range = normalize_range(range_key)
    offset = normalize_week_offset(week_offset)
    cal_year, cal_month = normalize_calendar_month(calendar_year, calendar_month, today)
    selected_day = normalize_calendar_day(calendar_day, cal_year, cal_month)
    start_iso, end_iso = _range_dates(
        active_range, today, offset, cal_year, cal_month
    )

    jobs: List[Dict[str, Any]] = []
    if staff:
        for booking in _load_rows(staff, start_iso, end_iso):
            jobs.append(_serialize_job(booking, today))

    if active_range == RANGE_TODAY:
        jobs.sort(
            key=lambda job: (
                job.get("start_hm") or "",
                job.get("customer_name") or "",
            )
        )
    else:
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
        jobs_label = (
            "No jobs today" if count == 0 else "{0} Job{1} today".format(count, "" if count == 1 else "s")
        )
    elif active_range == RANGE_CALENDAR:
        jobs_label = _month_heading(cal_year, cal_month)
    elif active_range == RANGE_WEEK:
        jobs_label = "{0} Job{1} This Week".format(count, "" if count == 1 else "s")
    elif active_range == RANGE_HISTORY:
        jobs_label = "{0} past Job{1}".format(count, "" if count == 1 else "s")
    else:
        jobs_label = "{0} Job{1}".format(count, "" if count == 1 else "s")

    calendar_view = None
    if active_range == RANGE_CALENDAR:
        calendar_view = _build_staff_calendar(
            jobs, cal_year, cal_month, today, selected_day
        )

    week_days = (
        _week_days(jobs, start_iso, end_iso, today)
        if active_range == RANGE_WEEK
        else []
    )
    history_weeks = (
        _history_weeks(jobs, today) if active_range == RANGE_HISTORY else []
    )
    summary = _hours_summary(jobs)
    work_days = _work_days_count(jobs)
    weekly_worked = None
    if active_range == RANGE_WEEK:
        weekly_worked = {
            "staff": staff,
            "week_start": start_iso,
            "week_end": end_iso,
            "week_label": _week_label(start_iso, end_iso),
            "minutes": int(round(summary["paid_hours"] * 60)),
            "display": summary["paid_display"],
            "estimated_minutes": int(round(summary["scheduled_hours"] * 60)),
            "estimated_display": summary["scheduled_display"],
            "work_days": work_days,
            "scheduled_display": summary["scheduled_display"],
            "actual_display": summary["actual_display"],
            "callout_display": summary["callout_display"],
            "paid_display": summary["paid_display"],
            "scheduled_hours": summary["scheduled_hours"],
            "actual_hours": summary["actual_hours"],
            "callout_hours": summary["callout_hours"],
            "paid_hours": summary["paid_hours"],
        }

    return {
        "staff": staff,
        "staff_options": crew_names,
        "range": active_range,
        "range_label": range_label,
        "range_tabs": RANGE_TABS,
        "start_date": start_iso,
        "end_date": end_iso,
        "week_offset": offset,
        "prev_week_offset": offset - 1,
        "next_week_offset": offset + 1,
        "calendar_year": cal_year,
        "calendar_month": cal_month,
        "calendar_day": selected_day,
        "calendar": calendar_view,
        "jobs": jobs,
        "job_count": count,
        "jobs_label": jobs_label,
        "week_days": week_days,
        "history_weeks": history_weeks,
        "weekly_worked": weekly_worked,
        "summary": summary,
        "work_days": work_days,
    }
