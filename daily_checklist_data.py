"""Phase 17 — daily job checklist for Confirmed / Paid bookings."""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import database as db
import double_booking
import invoice
import job_status
from booking_helpers import tel_href
from booking_times import display_finish_time, display_start_time
from crew import crew_from_storage, display_crew
from resource_conflicts import has_crew_conflict, has_truck_conflict
from dashboard_data import week_range
from integrations import payment_reminder_automation
from outstanding_invoices_data import _is_paid

CHECKLIST_STATUSES = frozenset({"Confirmed", "Paid"})

DAILY_CHECKLIST_FILTERS = [
    ("today", "Today"),
    ("tomorrow", "Tomorrow"),
    ("week", "This Week"),
]


def date_range_for_filter(filter_name: str, today: date) -> Tuple[str, str]:
    if filter_name == "tomorrow":
        day = today + timedelta(days=1)
        iso = day.isoformat()
        return iso, iso
    if filter_name == "week":
        start, end = week_range(today)
        return start.isoformat(), end.isoformat()
    iso = today.isoformat()
    return iso, iso


def _has_authorised_invoice(booking: Dict[str, Any]) -> bool:
    return payment_reminder_automation.has_authorised_invoice(booking)


def _payment_reminder_active(booking: Dict[str, Any]) -> bool:
    if _is_paid(booking):
        return False
    if payment_reminder_automation.is_reminders_cancelled(booking):
        return False
    if not _has_authorised_invoice(booking):
        return False
    sent = any(
        (booking.get(field) or "").strip()
        for field in payment_reminder_automation.SENT_FIELDS
    )
    if sent:
        return True
    return payment_reminder_automation.is_eligible(booking)


def _review_scheduled(booking: Dict[str, Any]) -> bool:
    if (booking.get("review_request_scheduled_at") or "").strip():
        return True
    if (booking.get("review_request_sent_at") or "").strip():
        return True
    booking_id = booking.get("id")
    if not booking_id:
        return False
    row = db.get_review_request_for_booking(int(booking_id))
    if not row:
        return False
    return (dict(row).get("status") or "").strip() in ("scheduled", "sent")


def _checklist_item(label: str, done: bool) -> Dict[str, Any]:
    return {"label": label, "done": bool(done)}


def build_checklist_sections(booking: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    status = job_status.display(booking)
    crew_names = crew_from_storage(booking.get("crew"))
    truck = (booking.get("truck_assigned") or "").strip()
    pickup = (booking.get("pickup_address") or "").strip()
    delivery = (booking.get("delivery_address") or "").strip()
    notes = (booking.get("notes") or "").strip()
    paid = _is_paid(booking)
    on_route_sms = bool((booking.get("eta_sms_sent_at") or "").strip())
    eta_confirmed = bool(booking.get("eta_minutes")) or bool(
        (booking.get("on_route_at") or "").strip()
    )
    completed = status in ("Completed", "Invoiced")

    before_job = [
        _checklist_item(
            "Customer confirmed",
            status in ("Confirmed", "Paid", "On Route", "Completed", "Invoiced"),
        ),
        _checklist_item("Crew assigned", bool(crew_names)),
        _checklist_item("Truck assigned", bool(truck)),
        _checklist_item(
            "Payment status checked",
            paid or _has_authorised_invoice(booking) or bool(
                (booking.get("payment_status") or "").strip()
            ),
        ),
        _checklist_item("Pickup address checked", bool(pickup)),
        _checklist_item("Delivery address checked", bool(delivery)),
        _checklist_item("Notes reviewed", not notes or bool(notes)),
    ]
    on_route = [
        _checklist_item("On Route SMS sent", on_route_sms),
        _checklist_item("ETA confirmed", eta_confirmed),
    ]
    after_job = [
        _checklist_item("Job marked Completed", completed),
        _checklist_item(
            "Invoice paid or payment reminder active",
            paid or _payment_reminder_active(booking),
        ),
        _checklist_item("Review request scheduled", _review_scheduled(booking)),
    ]
    return {
        "before_job": before_job,
        "on_route": on_route,
        "after_job": after_job,
    }


def build_warnings(booking: Dict[str, Any]) -> List[Dict[str, str]]:
    warnings: List[Dict[str, str]] = []
    phone = (booking.get("phone") or "").strip()
    if not phone or not tel_href(phone):
        warnings.append({"code": "no_phone", "label": "No phone number"})
    if not crew_from_storage(booking.get("crew")):
        warnings.append({"code": "no_crew", "label": "No crew assigned"})
    if not _is_paid(booking):
        warnings.append({"code": "not_paid", "label": "Not paid"})
    if not (booking.get("google_calendar_event_id") or "").strip():
        warnings.append({"code": "no_calendar", "label": "No calendar event"})
    if not _has_authorised_invoice(booking):
        warnings.append({"code": "no_invoice", "label": "No invoice"})
    badge = double_booking.badge_for_booking(booking)
    if badge == "conflict":
        warnings.append({"code": "double_booking", "label": "Double booking conflict"})
    if has_crew_conflict(booking):
        warnings.append({"code": "crew_conflict", "label": "Crew conflict"})
    if has_truck_conflict(booking):
        warnings.append({"code": "truck_conflict", "label": "Truck conflict"})
    return warnings


def _can_mark_completed(booking: Dict[str, Any]) -> bool:
    status = job_status.display(booking)
    return status in ("Confirmed", "Paid", "On Route", "In Progress")


def enrich_booking_row(row: Dict[str, Any]) -> Dict[str, Any]:
    booking = dict(row)
    sections = build_checklist_sections(booking)
    warnings = build_warnings(booking)
    incomplete_before = sum(1 for item in sections["before_job"] if not item["done"])
    return {
        "booking_id": int(booking["id"]),
        "customer_name": booking.get("customer_name") or "",
        "phone": booking.get("phone") or "",
        "email": booking.get("email") or "",
        "move_date": booking.get("move_date") or "",
        "start_display": display_start_time(booking),
        "finish_display": display_finish_time(booking),
        "status": job_status.display(booking),
        "payment_status": booking.get("payment_status") or "Unpaid",
        "crew_display": display_crew(booking),
        "truck_assigned": (booking.get("truck_assigned") or "").strip(),
        "pickup_address": booking.get("pickup_address") or "",
        "delivery_address": booking.get("delivery_address") or "",
        "notes": booking.get("notes") or "",
        "double_booking_badge": double_booking.badge_for_booking(booking),
        "checklist": sections,
        "warnings": warnings,
        "warning_count": len(warnings),
        "incomplete_before": incomplete_before,
        "can_mark_completed": _can_mark_completed(booking),
    }


def list_checklist_bookings(start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
    rows = db.list_between_dates(start_iso, end_iso)
    jobs: List[Dict[str, Any]] = []
    for row in rows:
        booking = dict(row)
        status = job_status.display(booking)
        if status not in CHECKLIST_STATUSES:
            continue
        jobs.append(enrich_booking_row(booking))
    jobs.sort(key=lambda j: (j["move_date"], j["start_display"], j["booking_id"]))
    return jobs


def build_daily_checklist(
    filter_name: str = "today",
    today: Optional[date] = None,
) -> Dict[str, Any]:
    if today is None:
        today = date.today()
    if filter_name not in {key for key, _ in DAILY_CHECKLIST_FILTERS}:
        filter_name = "today"
    start_iso, end_iso = date_range_for_filter(filter_name, today)
    jobs = list_checklist_bookings(start_iso, end_iso)
    warning_total = sum(j["warning_count"] for j in jobs)
    return {
        "filter": filter_name,
        "today": today.isoformat(),
        "range_start": start_iso,
        "range_end": end_iso,
        "jobs": jobs,
        "job_count": len(jobs),
        "warning_total": warning_total,
        "filters": DAILY_CHECKLIST_FILTERS,
    }
