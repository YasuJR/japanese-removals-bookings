"""Phase 20 — website quote submissions."""

from typing import Dict, List, Tuple

import automation
import config
import database as db
from integrations import company_config, email_send, gmail_config


def _booking_edit_url(booking_id: int) -> str:
    base = (config.APP_BASE_URL or "").rstrip("/")
    return "{0}/bookings/{1}/edit".format(base, booking_id)


def notify_admin(booking_id: int, data: Dict[str, str]) -> Tuple[bool, str]:
    admin_email = gmail_config.admin_notify_email()
    if not admin_email:
        return False, "No admin notify email configured."

    edit_url = _booking_edit_url(booking_id)
    subject = "New website quote request — review required (#{0})".format(booking_id)
    body = (
        "A new quote request was submitted from the website.\n\n"
        "Status: Pending (please review and confirm details)\n"
        "Source: Website\n"
        "Customer: {customer_name}\n"
        "Phone: {phone}\n"
        "Email: {email}\n"
        "Move date: {move_date}\n"
        "Pickup: {pickup_address}\n"
        "Delivery: {delivery_address}\n\n"
        "Notes:\n{notes}\n\n"
        "Review booking:\n{edit_url}\n"
    ).format(
        customer_name=data.get("customer_name") or "—",
        phone=data.get("phone") or "—",
        email=data.get("email") or "—",
        move_date=data.get("move_date") or "—",
        pickup_address=data.get("pickup_address") or "—",
        delivery_address=data.get("delivery_address") or "—",
        notes=data.get("notes") or "—",
        edit_url=edit_url,
    )
    return email_send.send_email(admin_email, subject, body)


def create_pending_booking_from_quote(data: Dict[str, str]) -> Tuple[bool, str, int]:
    """Create Pending booking from website quote — no calendar or Xero."""
    defaults = company_config.booking_form_defaults()
    booking_id = db.create_booking(
        customer_name=data["customer_name"],
        phone=data["phone"],
        email=data["email"],
        pickup_address=data["pickup_address"],
        delivery_address=data["delivery_address"],
        move_date=data["move_date"],
        num_movers=int(data.get("num_movers") or 2),
        notes=data.get("notes") or "",
        hourly_rate=float(defaults.get("hourly_rate") or 0),
        callout_fee=float(defaults.get("callout_fee") or 0),
        gst_enabled=1 if defaults.get("gst_enabled") else 0,
        status="Pending",
    )
    db.update_booking_integration_fields(
        booking_id,
        {"source": "Website"},
    )
    return True, "Quote request received.", booking_id


def submit_website_quote(data: Dict[str, str], ip_address: str = "") -> Tuple[bool, str, int, List[str]]:
    """Create booking, notify admin, record rate limit. Does not run calendar/Xero."""
    messages: List[str] = []
    ok, msg, booking_id = create_pending_booking_from_quote(data)
    if not ok:
        return False, msg, 0, messages

    if ip_address:
        db.record_quote_submission(ip_address)

    automation.log_event(
        automation.AUTOMATION_WEBSITE_QUOTE,
        automation.STATUS_SUCCESS,
        "Pending booking #{0} created from website quote.".format(booking_id),
        booking_id,
    )
    messages.append(msg)

    notify_ok, notify_msg = notify_admin(booking_id, data)
    automation.log_event(
        automation.AUTOMATION_WEBSITE_QUOTE_NOTIFY,
        automation.STATUS_SUCCESS if notify_ok else automation.STATUS_PARTIAL,
        notify_msg,
        booking_id,
    )
    messages.append(notify_msg)
    return True, "Thank you — we will contact you shortly.", booking_id, messages
