"""Shared booking form validation."""

from typing import Any, Dict, List, Tuple

import job_status
from booking_times import validate_times
from crew import crew_storage_value, parse_crew_from_form
from extra_charges import parse_extra_charges_from_form


def parse_booking_form(form: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Read and validate booking fields from a Flask form."""
    customer_name = form.get("customer_name", "")
    phone = form.get("phone", "")
    email = form.get("email", "")
    pickup_address = form.get("pickup_address", "")
    delivery_address = form.get("delivery_address", "")
    move_date = form.get("move_date", "")
    num_movers_raw = form.get("num_movers", "")
    notes = form.get("notes", "")
    start_time = form.get("start_time", "")
    finish_time = form.get("finish_time", "")
    duration_hours = form.get("duration_hours", "")

    errors = []
    if not customer_name:
        errors.append("Customer name is required.")
    if not pickup_address:
        errors.append("Pickup address is required.")
    if not delivery_address:
        errors.append("Delivery address is required.")
    if not move_date:
        errors.append("Move date is required.")

    num_movers = None
    try:
        num_movers = int(num_movers_raw)
        if num_movers < 1:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Number of movers must be at least 1.")

    start_norm, finish_norm, duration_storage, time_errors = validate_times(
        start_time, finish_time, duration_hours
    )
    errors.extend(time_errors)

    crew_names = parse_crew_from_form(form)

    hourly_rate = None
    callout_fee = None
    try:
        hourly_rate = float((form.get("hourly_rate") or "0").strip() or "0")
        if hourly_rate < 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Hourly rate must be a non-negative number.")

    try:
        callout_fee = float((form.get("callout_fee") or "0").strip() or "0")
        if callout_fee < 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Callout fee must be a non-negative number.")

    gst_enabled = 1 if form.get("gst_enabled") == "on" else 0
    payment_status = (form.get("payment_status") or "Unpaid").strip() or "Unpaid"
    invoice_status = (form.get("invoice_status") or "").strip()
    invoice_custom_text = (form.get("invoice_custom_text") or "").strip()
    invoice_bank_account_name = (form.get("invoice_bank_account_name") or "").strip()
    invoice_bank_bsb = (form.get("invoice_bank_bsb") or "").strip()
    invoice_bank_account = (form.get("invoice_bank_account") or "").strip()

    extra_charges, extra_errors = parse_extra_charges_from_form(form)
    errors.extend(extra_errors)

    status_raw = form.get("status", job_status.DEFAULT_STATUS)
    status = job_status.validate(status_raw)
    if status is None:
        errors.append("Invalid job status.")

    data = {
        "customer_name": customer_name,
        "phone": (phone or "").strip(),
        "email": (email or "").strip(),
        "pickup_address": pickup_address,
        "delivery_address": delivery_address,
        "move_date": move_date,
        "num_movers": num_movers,
        "notes": notes,
        "start_time": start_norm,
        "finish_time": finish_norm,
        "duration_hours": duration_storage,
        "crew": crew_names,
        "crew_csv": crew_storage_value(crew_names),
        "hourly_rate": hourly_rate if hourly_rate is not None else 0.0,
        "callout_fee": callout_fee if callout_fee is not None else 0.0,
        "gst_enabled": gst_enabled,
        "payment_status": payment_status,
        "invoice_status": invoice_status,
        "invoice_custom_text": invoice_custom_text,
        "invoice_bank_account_name": invoice_bank_account_name,
        "invoice_bank_bsb": invoice_bank_bsb,
        "invoice_bank_account": invoice_bank_account,
        "extra_charges": extra_charges,
        "truck_assigned": (form.get("truck_assigned") or "").strip(),
        "status": status or job_status.DEFAULT_STATUS,
    }
    return data, errors
