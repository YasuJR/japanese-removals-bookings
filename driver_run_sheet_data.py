"""Driver run sheet — one crew member's jobs for a single day."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

import job_status
from booking_helpers import pickup_suburb
from booking_times import display_start_time, effective_start_hm
from crew import active_crew_names, crew_from_storage
from crew_schedule_data import booking_duration_hours, format_hours_label
from display_dates import format_display_date
import database as db


def _parse_move_date(value: str, fallback: date) -> str:
    text = str(value or "").strip()[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return text
    except ValueError:
        return fallback.isoformat()


def _date_heading(move_date: str) -> str:
    parts = format_display_date(move_date)
    return "{0} {1}".format(parts["weekday"], parts["day_month"])


def _jobs_label(count: int, move_date: str, today: date) -> str:
    if move_date == today.isoformat():
        return "{0} Job{1} Today".format(count, "" if count == 1 else "s")
    return "{0} Job{1}".format(count, "" if count == 1 else "s")


def build_driver_run_sheet(
    crew_name: str,
    move_date: str,
    today: Optional[date] = None,
    truck_name: str = "",
) -> Dict[str, Any]:
    if today is None:
        today = date.today()

    crew_options = active_crew_names()
    crew = crew_name if crew_name in crew_options else crew_options[0]
    day_iso = _parse_move_date(move_date, today)
    truck_filter = (truck_name or "").strip()

    rows = db.list_by_date(day_iso)
    member_jobs: List[Dict[str, Any]] = []
    for row in rows:
        booking = dict(row)
        if crew not in crew_from_storage(booking.get("crew")):
            continue
        if truck_filter:
            assigned = (booking.get("truck_assigned") or "").strip()
            if assigned.lower() != truck_filter.lower():
                continue
        member_jobs.append(booking)

    member_jobs.sort(
        key=lambda b: (
            effective_start_hm(b),
            int(b.get("id") or 0),
        )
    )

    total_hours = 0.0
    jobs: List[Dict[str, Any]] = []
    for booking in member_jobs:
        hours = booking_duration_hours(booking)
        total_hours += hours
        suburb = pickup_suburb(booking.get("pickup_address"))
        phone = str(booking.get("phone") or "").strip()
        notes = str(booking.get("notes") or "").strip()
        jobs.append(
            {
                "booking_id": int(booking["id"]),
                "start_display": display_start_time(booking),
                "customer_name": str(booking.get("customer_name") or "").strip()
                or "—",
                "phone": phone,
                "pickup_address": str(booking.get("pickup_address") or "").strip(),
                "delivery_address": str(
                    booking.get("delivery_address") or ""
                ).strip(),
                "suburb_badge": suburb.upper() if suburb != "—" else "",
                "status": job_status.display(booking),
                "notes": notes,
                "hours": hours,
            }
        )

    count = len(jobs)
    return {
        "crew": crew,
        "truck": truck_filter,
        "move_date": day_iso,
        "date_heading": _date_heading(day_iso),
        "is_today": day_iso == today.isoformat(),
        "jobs": jobs,
        "job_count": count,
        "jobs_label": _jobs_label(count, day_iso, today),
        "total_hours": round(total_hours, 1),
        "hours_label": format_hours_label(total_hours),
    }
