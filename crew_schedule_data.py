"""Build crew schedule view data grouped by team member."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import database as db
import job_status
from booking_helpers import pickup_suburb
from booking_times import (
    display_start_time,
    effective_start_hm,
    inferred_duration_hours,
    parse_duration_hours,
)
from crew import active_crew_names, crew_from_storage
import double_booking
from dashboard_data import week_range
from display_dates import format_display_date

RANGE_OPTIONS: List[Tuple[str, str]] = [
    ("today", "Today"),
    ("tomorrow", "Tomorrow"),
    ("this_week", "This Week"),
    ("next_week", "Next Week"),
    ("custom", "Custom Date"),
]


def booking_duration_hours(booking: Dict[str, Any]) -> float:
    stored = parse_duration_hours(booking.get("duration_hours"))
    if stored is not None:
        return stored
    inferred = inferred_duration_hours(booking)
    if inferred is not None:
        return inferred
    return 0.0


def format_hours_label(hours: float) -> str:
    if hours <= 0:
        return "0 Hours"
    if hours == int(hours):
        return "{0} Hours".format(int(hours))
    return "{0:.1f} Hours".format(hours)


def _parse_custom_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def resolve_date_range(
    range_name: str,
    today: date = None,
    custom_date: Optional[str] = None,
) -> Tuple[date, date, str, str]:
    if today is None:
        today = date.today()
    name = (range_name or "this_week").strip().lower()

    if name == "today":
        return today, today, "Today", ""
    if name == "tomorrow":
        day = today + timedelta(days=1)
        return day, day, "Tomorrow", ""
    if name == "custom":
        picked = _parse_custom_date(custom_date)
        if picked is None:
            return today, today, "Custom date", ""
        label = picked.strftime("%a %d %b %Y")
        return picked, picked, label, picked.isoformat()
    if name == "next_week":
        this_monday, _ = week_range(today)
        next_monday = this_monday + timedelta(days=7)
        next_sunday = next_monday + timedelta(days=6)
        label = "Next Week ({0} – {1})".format(
            next_monday.strftime("%d %b"),
            next_sunday.strftime("%d %b"),
        )
        return next_monday, next_sunday, label, ""

    week_start, week_end = week_range(today)
    label = "This Week ({0} – {1})".format(
        week_start.strftime("%d %b"),
        week_end.strftime("%d %b"),
    )
    return week_start, week_end, label, ""


def _booking_sort_key(booking: Dict[str, Any]) -> tuple:
    return (
        booking.get("move_date") or "",
        effective_start_hm(booking),
        int(booking.get("id") or 0),
    )


def _times_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    a_start, a_end = double_booking.booking_event_datetimes(a)
    b_start, b_end = double_booking.booking_event_datetimes(b)
    return a_start < b_end and b_start < a_end


def _conflict_ids_for_member(jobs: List[Dict[str, Any]]) -> Set[int]:
    conflicts: Set[int] = set()
    by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        by_date[job["move_date"]].append(job)

    for day_jobs in by_date.values():
        for i, first in enumerate(day_jobs):
            for second in day_jobs[i + 1 :]:
                if _times_overlap(first, second):
                    conflicts.add(int(first["id"]))
                    conflicts.add(int(second["id"]))
    return conflicts


def _format_date_label(booking: Dict[str, Any]) -> str:
    parts = format_display_date(booking.get("move_date"))
    return "{0} {1}".format(parts["weekday"], parts["day_month"])


def build_crew_schedule(
    range_name: str,
    today: date = None,
    custom_date: Optional[str] = None,
) -> Dict[str, Any]:
    valid_ranges = {key for key, _ in RANGE_OPTIONS}
    active_range = (range_name or "this_week").strip().lower()
    if active_range not in valid_ranges:
        active_range = "this_week"

    start, end, range_label, custom_iso = resolve_date_range(
        active_range, today, custom_date
    )
    if active_range == "custom" and not custom_iso:
        active_range = "today"
        start, end, range_label, custom_iso = resolve_date_range("today", today)

    rows = db.list_between_dates(start.isoformat(), end.isoformat())

    bookings: List[Dict[str, Any]] = []
    for row in rows:
        booking = dict(row)
        crew_names = crew_from_storage(booking.get("crew"))
        if not crew_names:
            continue
        booking["crew_list"] = crew_names
        bookings.append(booking)

    crew_sections: List[Dict[str, Any]] = []
    workload: List[Dict[str, Any]] = []

    for member in active_crew_names():
        member_jobs = [b for b in bookings if member in b["crew_list"]]
        member_jobs.sort(key=_booking_sort_key)
        conflicts = _conflict_ids_for_member(member_jobs)
        total_hours = 0.0

        entries = []
        for job in member_jobs:
            job_id = int(job["id"])
            hours = booking_duration_hours(job)
            total_hours += hours
            suburb = pickup_suburb(job.get("pickup_address"))
            entries.append(
                {
                    "booking_id": job_id,
                    "date_label": _format_date_label(job),
                    "start_display": display_start_time(job),
                    "customer_name": str(job.get("customer_name") or "").strip()
                    or "—",
                    "suburb": suburb,
                    "suburb_badge": suburb.upper() if suburb != "—" else "—",
                    "status": job_status.display(job),
                    "hours": hours,
                    "is_conflict": job_id in conflicts,
                }
            )

        crew_sections.append(
            {
                "name": member,
                "jobs": entries,
                "job_count": len(entries),
                "total_hours": round(total_hours, 1),
                "hours_label": format_hours_label(total_hours),
            }
        )
        workload.append(
            {
                "name": member,
                "job_count": len(entries),
                "jobs_label": "{0} Job{1}".format(
                    len(entries), "" if len(entries) == 1 else "s"
                ),
                "total_hours": round(total_hours, 1),
                "hours_label": format_hours_label(total_hours),
            }
        )

    return {
        "active_range": active_range,
        "range_label": range_label,
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "custom_date": custom_iso,
        "crew_sections": crew_sections,
        "workload": workload,
        "total_assigned_jobs": len(bookings),
    }
