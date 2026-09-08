"""Staff Portal owner/admin edit permissions and booking updates."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import abort, flash, g, redirect, request, url_for

import auth
import database as db
import job_status
import staff_job_times
from booking_times import duration_hours_from_times, normalize_time_input
from crew import active_crew_names, crew_storage_value, merge_crew_for_edit

STAFF_PORTAL_STATUS_OPTIONS: List[str] = [
    "Confirmed",
    "On Route",
    "In Progress",
    "Completed",
    "Invoiced",
    "Paid",
    "Cancelled",
]


def can_manage_staff_portal(user: Any) -> bool:
    """Owner/Admin may edit Staff Portal job fields and star points."""
    return auth.is_admin_user(user)


def owner_required(view: Callable) -> Callable:
    """Office login required; non-admin users receive HTTP 403."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if g.get("user") is None:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login", next=request.path))
        if not can_manage_staff_portal(g.user):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _parse_callout_hours(form: Any) -> Tuple[Optional[float], List[str]]:
    raw = form.get("callout_hours") if hasattr(form, "get") else ""
    raw = str(raw or "").strip()
    if not raw:
        return 0.0, []
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, ["Enter call-out hours as a number (e.g. 0.5)."]
    if value < 0:
        return None, ["Call-out hours cannot be negative."]
    return round(value, 2), []


def parse_owner_job_edit_form(
    form: Any, booking_row: Any
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    start = normalize_time_input(form.get("start_time") if hasattr(form, "get") else "")
    finish = normalize_time_input(form.get("finish_time") if hasattr(form, "get") else "")

    status = job_status.validate(form.get("status") if hasattr(form, "get") else "")
    if status is None:
        errors.append("Invalid job status.")
    elif status not in STAFF_PORTAL_STATUS_OPTIONS:
        errors.append("Status is not allowed from Staff Portal.")

    crew_names = merge_crew_for_edit(_row_value(booking_row, "crew"), form)
    if not crew_names:
        errors.append("Select at least one crew member.")

    notes = str(form.get("notes") if hasattr(form, "get") else "").strip()
    callout_hours, callout_errors = _parse_callout_hours(form)
    if callout_errors:
        errors.extend(callout_errors)

    actual_start, actual_finish, minutes, actual_errors = (
        staff_job_times.parse_actual_times_from_form(form)
    )
    errors.extend(actual_errors)

    if errors or callout_hours is None or status is None:
        return None, errors

    duration = duration_hours_from_times(start, finish)
    duration_hours = (
        str(duration) if duration is not None else str(_row_value(booking_row, "duration_hours"))
    )

    try:
        hourly_rate = float(_row_value(booking_row, "hourly_rate") or 0)
    except (TypeError, ValueError):
        hourly_rate = 0.0
    callout_fee = round(callout_hours * hourly_rate, 2) if hourly_rate > 0 else 0.0

    return {
        "start_time": start,
        "finish_time": finish,
        "duration_hours": duration_hours,
        "crew_csv": crew_storage_value(crew_names),
        "status": status,
        "notes": notes,
        "callout_fee": callout_fee,
        "actual_start": actual_start,
        "actual_finish": actual_finish,
        "actual_minutes": minutes,
    }, []


def save_owner_job_edit(booking_id: int, form: Any) -> Tuple[bool, List[str]]:
    row = db.get_booking(booking_id)
    if row is None:
        return False, ["Booking not found."]

    data, errors = parse_owner_job_edit_form(form, row)
    if errors or data is None:
        return False, errors

    ok = db.update_booking(
        booking_id=booking_id,
        customer_name=str(_row_value(row, "customer_name")),
        phone=str(_row_value(row, "phone")),
        email=str(_row_value(row, "email")),
        pickup_address=str(_row_value(row, "pickup_address")),
        delivery_address=str(_row_value(row, "delivery_address")),
        move_date=str(_row_value(row, "move_date")),
        num_movers=int(_row_value(row, "num_movers") or 0),
        notes=data["notes"],
        start_time=data["start_time"],
        finish_time=data["finish_time"],
        duration_hours=data["duration_hours"],
        crew=data["crew_csv"],
        hourly_rate=float(_row_value(row, "hourly_rate") or 0),
        callout_fee=data["callout_fee"],
        gst_enabled=int(_row_value(row, "gst_enabled") or 1),
        payment_status=str(_row_value(row, "payment_status") or "Unpaid"),
        invoice_status=str(_row_value(row, "invoice_status") or ""),
        status=data["status"],
    )
    if not ok:
        return False, ["Could not save booking."]

    db.save_booking_actual_times(
        booking_id,
        data["actual_start"],
        data["actual_finish"],
        data["actual_minutes"],
    )
    return True, []
