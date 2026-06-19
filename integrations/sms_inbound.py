"""Phase 21 — Twilio inbound SMS to booking or draft lead."""

from typing import Any, Dict, List, Tuple

import automation
import database as db
import sms_inbound_parser
from integrations import company_config, gmail_config, email_send


def _booking_edit_url(booking_id: int) -> str:
    import config

    base = (config.APP_BASE_URL or "").rstrip("/")
    return "{0}/bookings/{1}/edit".format(base, booking_id)


def _create_pending_booking(fields: Dict[str, str]) -> int:
    defaults = company_config.booking_form_defaults()
    booking_id = db.create_booking(
        customer_name=fields["customer_name"],
        phone=fields["phone"],
        email=fields.get("email") or defaults.get("email") or "",
        pickup_address=fields["pickup_address"],
        delivery_address=fields["delivery_address"],
        move_date=fields["move_date"],
        num_movers=2,
        notes=fields.get("notes") or "",
        hourly_rate=float(defaults.get("hourly_rate") or 0),
        callout_fee=float(defaults.get("callout_fee") or 0),
        gst_enabled=1 if defaults.get("gst_enabled") else 0,
        status="Pending",
    )
    db.update_booking_integration_fields(
        booking_id,
        {"source": fields.get("source") or "SMS"},
    )
    return booking_id


def notify_admin_sms(booking_id: int, fields: Dict[str, str]) -> Tuple[bool, str]:
    admin_email = gmail_config.admin_notify_email()
    if not admin_email:
        return False, "No admin notify email configured."
    subject = "New SMS booking — review required (#{0})".format(booking_id)
    body = (
        "A new Pending booking was created from an inbound SMS.\n\n"
        "Source: SMS\n"
        "Confidence: {confidence:.0f}%\n"
        "Customer: {customer_name}\n"
        "Phone: {phone}\n"
        "Move date: {move_date}\n"
        "Pickup: {pickup_address}\n"
        "Delivery: {delivery_address}\n\n"
        "Message:\n{raw_message}\n\n"
        "Review booking:\n{edit_url}\n"
    ).format(
        confidence=float(fields.get("confidence") or 0),
        customer_name=fields.get("customer_name") or "—",
        phone=fields.get("phone") or "—",
        move_date=fields.get("move_date") or "—",
        pickup_address=fields.get("pickup_address") or "—",
        delivery_address=fields.get("delivery_address") or "—",
        raw_message=fields.get("raw_message") or fields.get("notes") or "—",
        edit_url=_booking_edit_url(booking_id),
    )
    return email_send.send_email(admin_email, subject, body)


def notify_admin_lead(lead_id: int, fields: Dict[str, str]) -> Tuple[bool, str]:
    admin_email = gmail_config.admin_notify_email()
    if not admin_email:
        return False, "No admin notify email configured."
    import config

    base = (config.APP_BASE_URL or "").rstrip("/")
    leads_url = "{0}/leads".format(base)
    subject = "New SMS draft lead — review required (#{0})".format(lead_id)
    body = (
        "An inbound SMS was parsed with low confidence and saved as a draft lead.\n\n"
        "Confidence: {confidence:.0f}%\n"
        "Phone: {phone}\n"
        "Move date: {move_date}\n\n"
        "Message:\n{raw_message}\n\n"
        "Review leads:\n{leads_url}\n"
    ).format(
        confidence=float(fields.get("confidence") or 0),
        phone=fields.get("phone") or "—",
        move_date=fields.get("move_date") or "—",
        raw_message=fields.get("raw_message") or "—",
        leads_url=leads_url,
    )
    return email_send.send_email(admin_email, subject, body)


def process_inbound_sms(from_number: str, body: str) -> Dict[str, Any]:
    """Create Pending booking or draft lead from inbound SMS."""
    fields = sms_inbound_parser.parse_inbound_sms(from_number, body)
    messages: List[str] = []

    if sms_inbound_parser.meets_booking_threshold(fields):
        booking_id = _create_pending_booking(fields)
        automation.log_event(
            automation.AUTOMATION_SMS_INBOUND_BOOKING,
            automation.STATUS_SUCCESS,
            "Pending booking #{0} from SMS ({1:.0f}% confidence).".format(
                booking_id, fields["confidence"]
            ),
            booking_id,
        )
        notify_ok, notify_msg = notify_admin_sms(booking_id, fields)
        automation.log_event(
            automation.AUTOMATION_SMS_INBOUND_NOTIFY,
            automation.STATUS_SUCCESS if notify_ok else automation.STATUS_PARTIAL,
            notify_msg,
            booking_id,
        )
        messages.append(notify_msg)
        return {
            "kind": "booking",
            "booking_id": booking_id,
            "confidence": fields["confidence"],
            "messages": messages,
        }

    lead_id = db.create_draft_lead(
        customer_name=fields.get("customer_name") or "",
        phone=fields.get("phone") or "",
        email=fields.get("email") or "",
        move_date=fields.get("move_date") or "",
        pickup_address=fields.get("pickup_address") or "",
        delivery_address=fields.get("delivery_address") or "",
        notes=fields.get("notes") or "",
        source="SMS",
        confidence=float(fields.get("confidence") or 0),
        raw_message=fields.get("raw_message") or "",
    )
    automation.log_event(
        automation.AUTOMATION_SMS_INBOUND_LEAD,
        automation.STATUS_SUCCESS,
        "Draft lead #{0} from SMS ({1:.0f}% confidence).".format(
            lead_id, fields["confidence"]
        ),
    )
    notify_ok, notify_msg = notify_admin_lead(lead_id, fields)
    automation.log_event(
        automation.AUTOMATION_SMS_INBOUND_NOTIFY,
        automation.STATUS_SUCCESS if notify_ok else automation.STATUS_PARTIAL,
        notify_msg,
    )
    messages.append(notify_msg)
    return {
        "kind": "lead",
        "lead_id": lead_id,
        "confidence": fields["confidence"],
        "messages": messages,
    }


def convert_lead_to_booking(lead_id: int) -> Tuple[bool, str, int]:
    row = db.get_draft_lead(lead_id)
    if not row:
        return False, "Lead not found.", 0
    lead = dict(row)
    if (lead.get("status") or "").strip() == "converted":
        booking_id = int(lead.get("booking_id") or 0)
        return True, "Lead already converted.", booking_id

    fields = {
        "customer_name": lead.get("customer_name") or "SMS enquiry",
        "phone": lead.get("phone") or "",
        "email": lead.get("email") or "",
        "move_date": lead.get("move_date") or default_move_date(),
        "pickup_address": lead.get("pickup_address") or "TBC",
        "delivery_address": lead.get("delivery_address") or "TBC",
        "notes": lead.get("notes") or lead.get("raw_message") or "",
        "source": lead.get("source") or "SMS",
    }
    booking_id = _create_pending_booking(fields)
    db.mark_lead_converted(lead_id, booking_id)
    automation.log_event(
        automation.AUTOMATION_SMS_LEAD_CONVERTED,
        automation.STATUS_SUCCESS,
        "Lead #{0} converted to booking #{1}.".format(lead_id, booking_id),
        booking_id,
    )
    return True, "Lead converted to booking #{0}.".format(booking_id), booking_id


def default_move_date() -> str:
    from integrations import gmail_parser

    return gmail_parser.default_move_date()


def reply_template_for_lead(lead: Dict[str, str]) -> str:
    name = (lead.get("customer_name") or "").strip() or "there"
    move_date = (lead.get("move_date") or "").strip() or "your move"
    return (
        "Hi {0}, thanks for your SMS about your move on {1}. "
        "This is Japanese Removals — we'll call you shortly to confirm pickup, "
        "delivery and provide a quote. Reply STOP to opt out."
    ).format(name, move_date)
