"""Booking calendar data for month/week/day views."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import database as db
import invoice
import job_status
from booking_times import (
    display_finish_time,
    display_start_time,
    effective_start_hm,
    format_time_12h,
    parse_duration_hours,
)
from crew import active_crew_names, crew_from_storage, display_crew
import double_booking

STATUS_FILTERS = [
    ("all", "All"),
    ("Quote", "Quote"),
    ("Confirmed", "Confirmed"),
    ("Completed", "Completed"),
]

PAYMENT_FILTERS = [
    ("all", "All"),
    ("Paid", "Paid"),
    ("Unpaid", "Unpaid"),
]


def _booking_dict(row) -> Dict[str, Any]:
    return dict(row) if row else {}


def _time_to_minutes(hm: str) -> int:
    parts = (hm or "08:00").split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (IndexError, ValueError):
        return 8 * 60


def calendar_event(booking: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize one booking for the calendar UI."""
    b = dict(booking)
    bid = int(b["id"])
    status = job_status.display(b)
    payment = invoice.normalize_payment_status(b.get("payment_status"))
    badge = double_booking.badge_for_booking(b)
    start_hm = effective_start_hm(b)
    finish_hm = display_finish_time(b)
    crew_list = crew_from_storage(b.get("crew"))
    return {
        "id": bid,
        "customer_name": b.get("customer_name") or "—",
        "move_date": (b.get("move_date") or "")[:10],
        "start_time": start_hm,
        "finish_time": finish_hm,
        "start_display": format_time_12h(start_hm),
        "finish_display": format_time_12h(finish_hm),
        "time_range": "{0}–{1}".format(
            format_time_12h(start_hm), format_time_12h(finish_hm)
        ),
        "num_movers": int(b.get("num_movers") or 0),
        "crew": display_crew(b),
        "crew_list": crew_list,
        "status": status,
        "status_class": _status_calendar_class(b, badge),
        "payment_status": payment,
        "truck_assigned": (b.get("truck_assigned") or "").strip(),
        "conflict_badge": badge,
        "has_conflict": badge == "conflict",
        "edit_url": "/bookings/{0}/edit".format(bid),
        "start_minutes": _time_to_minutes(start_hm),
        "duration_hours": parse_duration_hours(b.get("duration_hours")) or 1.0,
    }


def _status_calendar_class(booking: Dict[str, Any], conflict_badge: Optional[str]) -> str:
    if conflict_badge == "override":
        return "cal-override"
    if conflict_badge == "conflict":
        return "cal-conflict"
    status = job_status.display(booking)
    payment = invoice.normalize_payment_status(booking.get("payment_status"))
    if status == "Cancelled":
        return "cal-cancelled"
    if status == "Completed":
        return "cal-completed"
    if status == "Paid" or payment == invoice.PAYMENT_STATUS_PAID:
        return "cal-paid"
    if payment == invoice.PAYMENT_STATUS_UNPAID and status in (
        "Confirmed",
        "On Route",
        "In Progress",
        "Completed",
        "Invoiced",
    ):
        return "cal-unpaid"
    if status == "Confirmed":
        return "cal-confirmed"
    return "cal-quote"


def _passes_filters(
    event: Dict[str, Any],
    *,
    status_filter: str,
    crew_filter: str,
    truck_filter: str,
    payment_filter: str,
) -> bool:
    if status_filter and status_filter != "all":
        if event["status"] != status_filter:
            return False
    if crew_filter and crew_filter != "all":
        if crew_filter not in (event.get("crew_list") or []):
            return False
    if truck_filter and truck_filter != "all":
        if event.get("truck_assigned") != truck_filter:
            return False
    if payment_filter and payment_filter != "all":
        if event.get("payment_status") != payment_filter:
            return False
    return True


def _month_bounds(year: int, month: int) -> Tuple[date, date]:
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    # Grid includes leading/trailing days for full weeks (Sunday start).
    start = first - timedelta(days=(first.weekday() + 1) % 7)
    end = last + timedelta(days=(6 - ((last.weekday() + 1) % 7)))
    return start, end


def _week_bounds(anchor: date) -> Tuple[date, date]:
    start = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
    return start, start + timedelta(days=6)


def load_events(
    start: date,
    end: date,
    *,
    status_filter: str = "all",
    crew_filter: str = "all",
    truck_filter: str = "all",
    payment_filter: str = "all",
) -> List[Dict[str, Any]]:
    rows = db.list_between_dates(start.isoformat(), end.isoformat())
    events = []
    for row in rows:
        ev = calendar_event(_booking_dict(row))
        if _passes_filters(
            ev,
            status_filter=status_filter,
            crew_filter=crew_filter,
            truck_filter=truck_filter,
            payment_filter=payment_filter,
        ):
            events.append(ev)
    events.sort(key=lambda e: (e["move_date"], e["start_minutes"], e["customer_name"]))
    return events


def day_detail(date_iso: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    day_events = [e for e in events if e["move_date"] == date_iso]
    crews: set = set()
    trucks: set = set()
    for ev in day_events:
        crews.update(ev.get("crew_list") or [])
        if ev.get("truck_assigned"):
            trucks.add(ev["truck_assigned"])
    conflicts = [e for e in day_events if e.get("has_conflict")]
    return {
        "date": date_iso,
        "date_display": _format_day(date_iso),
        "bookings": day_events,
        "crews": sorted(crews),
        "trucks": sorted(trucks),
        "conflicts": conflicts,
        "has_conflict": bool(conflicts),
        "booking_count": len(day_events),
    }


def _format_day(date_iso: str) -> str:
    try:
        return datetime.strptime(date_iso[:10], "%Y-%m-%d").strftime("%A %d %B %Y")
    except ValueError:
        return date_iso


def build_calendar_context(
    *,
    view: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    day: Optional[int] = None,
    status_filter: str = "all",
    crew_filter: str = "all",
    truck_filter: str = "all",
    payment_filter: str = "all",
    today: Optional[date] = None,
) -> Dict[str, Any]:
    today = today or date.today()
    year = year or today.year
    month = month or today.month
    day = day or today.day

    view = (view or "month").lower()
    if view not in ("month", "week", "day"):
        view = "month"

    if view == "day":
        anchor = date(year, month, min(day, monthrange(year, month)[1]))
        range_start, range_end = anchor, anchor
    elif view == "week":
        anchor = date(year, month, min(day, monthrange(year, month)[1]))
        range_start, range_end = _week_bounds(anchor)
    else:
        range_start, range_end = _month_bounds(year, month)
        anchor = date(year, month, 1)

    events = load_events(
        range_start,
        range_end,
        status_filter=status_filter,
        crew_filter=crew_filter,
        truck_filter=truck_filter,
        payment_filter=payment_filter,
    )

    events_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        events_by_date.setdefault(ev["move_date"], []).append(ev)

    month_grid: List[List[Dict[str, Any]]] = []
    if view == "month":
        cursor = range_start
        while cursor <= range_end:
            week_row = []
            for _ in range(7):
                iso = cursor.isoformat()
                day_events = events_by_date.get(iso, [])
                week_row.append(
                    {
                        "date": iso,
                        "day": cursor.day,
                        "in_month": cursor.month == month,
                        "is_today": cursor == today,
                        "bookings": day_events,
                        "has_conflict": any(e.get("has_conflict") for e in day_events),
                        "count": len(day_events),
                    }
                )
                cursor += timedelta(days=1)
            month_grid.append(week_row)

    week_days = []
    if view == "week":
        cursor = range_start
        while cursor <= range_end:
            iso = cursor.isoformat()
            week_days.append(
                {
                    "date": iso,
                    "label": cursor.strftime("%a %d"),
                    "is_today": cursor == today,
                    "bookings": events_by_date.get(iso, []),
                    "has_conflict": any(
                        e.get("has_conflict") for e in events_by_date.get(iso, [])
                    ),
                }
            )
            cursor += timedelta(days=1)

    trucks = sorted(
        {
            (dict(r).get("truck_assigned") or "").strip()
            for r in db.list_trucks(active_only=True)
        }
        | {ev.get("truck_assigned") or "" for ev in events}
    )
    trucks = [t for t in trucks if t]

    title = anchor.strftime("%B %Y")
    if view == "day":
        title = _format_day(anchor.isoformat())

    if view == "week":
        prev_anchor = anchor - timedelta(days=7)
        next_anchor = anchor + timedelta(days=7)
    elif view == "day":
        prev_anchor = anchor - timedelta(days=1)
        next_anchor = anchor + timedelta(days=1)
    else:
        first_of_month = date(year, month, 1)
        prev_month_date = first_of_month - timedelta(days=1)
        next_month_date = date(year, month, monthrange(year, month)[1]) + timedelta(days=1)
        prev_anchor = prev_month_date
        next_anchor = next_month_date

    return {
        "view": view,
        "year": year,
        "month": month,
        "day": anchor.day,
        "anchor_date": anchor.isoformat(),
        "prev_year": prev_anchor.year,
        "prev_month": prev_anchor.month,
        "prev_day": prev_anchor.day,
        "next_year": next_anchor.year,
        "next_month": next_anchor.month,
        "next_day": next_anchor.day,
        "title": title,
        "today_iso": today.isoformat(),
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "events": events,
        "events_by_date": events_by_date,
        "month_grid": month_grid,
        "week_days": week_days,
        "weekday_labels": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
        "time_slots": ["{0}:00".format(h) for h in range(6, 20)],
        "crew_options": active_crew_names(),
        "truck_options": trucks,
        "status_filters": STATUS_FILTERS,
        "payment_filters": PAYMENT_FILTERS,
        "filters": {
            "status": status_filter,
            "crew": crew_filter,
            "truck": truck_filter,
            "payment": payment_filter,
        },
    }
