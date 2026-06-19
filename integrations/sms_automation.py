"""SMS automation — triggers, templates, and delivery logging."""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import automation
import config
import database as db
import invoice
import job_status
from booking_times import display_start_time
from display_dates import format_display_date
from integrations import sms, sms_config
from outstanding_invoices_data import (
    _is_overdue,
    due_date_for,
    has_invoice,
)

SENT_FIELD_BY_TEMPLATE = {
    "booking_confirmation": "sms_confirmation_sent_at",
    "booking_confirmed": "sms_booking_confirmed_sent_at",
    "booking_reminder": "sms_move_reminder_sent_at",
    "thank_you": "sms_thank_you_sent_at",
    "payment_reminder": "sms_payment_reminder_sent_at",
    "payment_confirmation": "sms_payment_confirmation_sent_at",
    "eta_on_route": "eta_sms_sent_at",
}

AUTOMATION_TYPE_BY_TEMPLATE = {
    "booking_confirmation": automation.AUTOMATION_SMS_BOOKING_CONFIRMATION,
    "booking_confirmed": automation.AUTOMATION_SMS_BOOKING_CONFIRMED,
    "booking_reminder": automation.AUTOMATION_SMS_BOOKING_REMINDER,
    "thank_you": automation.AUTOMATION_SMS_THANK_YOU,
    "payment_reminder": automation.AUTOMATION_SMS_PAYMENT_REMINDER,
    "payment_confirmation": automation.AUTOMATION_SMS_PAYMENT_CONFIRMATION,
    "eta_on_route": automation.AUTOMATION_ETA_SMS_SENT,
}


def _first_name(booking: Dict[str, Any]) -> str:
    name = (booking.get("customer_name") or "").strip()
    if not name:
        return "Customer"
    return name.split()[0]


def _invoice_link(booking: Dict[str, Any]) -> str:
    base = (config.APP_BASE_URL or "").rstrip("/")
    booking_id = booking.get("id")
    if not booking_id:
        return base
    return "{0}/bookings/{1}/invoice/preview".format(base, booking_id)


def _booking_amount(booking: Dict[str, Any]) -> str:
    totals = invoice.calculate_invoice_totals(
        invoice.resolve_booking_invoice(booking)
    )
    return invoice.format_aud(totals["total"])


def _move_date_display(booking: Dict[str, Any]) -> str:
    raw = (booking.get("move_date") or "").strip()
    parts = format_display_date(raw)
    if parts.get("weekday") and parts["weekday"] != "—":
        return "{0} {1}".format(parts["weekday"], parts["day_month"])
    return raw


def render_template(booking: Dict[str, Any], template_key: str) -> str:
    template = sms_config.get_template(template_key)
    company_phone = config.COMPANY_PHONE or "us"
    values = {
        "customer_name": (booking.get("customer_name") or "").strip() or "Customer",
        "first_name": _first_name(booking),
        "move_date": _move_date_display(booking),
        "start_time": display_start_time(booking),
        "driver_name": (booking.get("driver_name") or "").strip() or "Your driver",
        "eta_minutes": str(int(booking.get("eta_minutes") or 0) or 0),
        "pickup": (booking.get("pickup_address") or "").strip(),
        "delivery": (booking.get("delivery_address") or "").strip(),
        "ref": str(booking.get("id") or ""),
        "due_date": due_date_for(booking),
        "amount": _booking_amount(booking),
        "invoice_link": _invoice_link(booking),
        "company_name": config.COMPANY_NAME,
        "company_phone": company_phone,
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise ValueError("Unknown placeholder in template: {0}".format(exc)) from exc


def _already_sent(booking: Dict[str, Any], template_key: str) -> bool:
    field = SENT_FIELD_BY_TEMPLATE.get(template_key)
    if not field:
        return False
    return bool((booking.get(field) or "").strip())


def _mark_sent(booking_id: int, template_key: str) -> None:
    field = SENT_FIELD_BY_TEMPLATE.get(template_key)
    if not field:
        return
    db.update_booking_integration_fields(
        booking_id,
        {field: datetime.utcnow().isoformat(timespec="seconds")},
    )


def send_template_sms(
    booking: Dict[str, Any],
    template_key: str,
    *,
    force: bool = False,
    override_phone: Optional[str] = None,
    automation_log_type: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Send one automated SMS. Returns (ok, message).
    Skips when trigger disabled, already sent, or missing phone unless force=True.
    """
    booking_id = int(booking["id"])
    automation_type = automation_log_type or AUTOMATION_TYPE_BY_TEMPLATE.get(
        template_key, "sms_{0}".format(template_key)
    )

    if not sms.is_configured():
        msg = "SMS not configured — add Twilio settings to .env"
        automation.log_event(automation_type, automation.STATUS_ERROR, msg, booking_id)
        return False, msg

    if not force and not sms_config.is_trigger_enabled(template_key):
        return False, "SMS automation disabled for this trigger."

    if not force and _already_sent(booking, template_key):
        return False, "SMS already sent for this booking."

    phone = (override_phone or booking.get("phone") or "").strip()
    if not phone:
        msg = "No phone number on booking."
        automation.log_event(automation_type, automation.STATUS_ERROR, msg, booking_id)
        return False, msg

    try:
        body = render_template(booking, template_key)
    except ValueError as exc:
        automation.log_event(
            automation_type, automation.STATUS_ERROR, str(exc), booking_id
        )
        return False, str(exc)

    send_booking = dict(booking)
    if override_phone:
        send_booking["phone"] = override_phone

    ok, msg, _sid = sms.send_message(
        send_booking,
        body,
        automation_type=automation_type,
        template_key=template_key,
    )
    if ok:
        if not override_phone:
            _mark_sent(booking_id, template_key)
        automation.log_event(
            automation_type,
            automation.STATUS_SENT,
            msg,
            booking_id,
        )
    else:
        automation.log_event(
            automation_type,
            automation.STATUS_ERROR,
            msg,
            booking_id,
        )
    return ok, msg


def maybe_send_payment_confirmation(booking: Dict[str, Any]) -> Tuple[bool, str]:
    """Send payment confirmation SMS once after Stripe payment (webhook)."""
    booking_id = int(booking["id"])
    if _already_sent(booking, "payment_confirmation"):
        return True, "Payment confirmation SMS already sent."

    if not sms_config.is_trigger_enabled("payment_confirmation"):
        msg = "Payment confirmation SMS disabled in Settings → SMS."
        automation.log_event(
            automation.AUTOMATION_SMS_PAYMENT_CONFIRMATION,
            automation.STATUS_PARTIAL,
            msg,
            booking_id,
        )
        return False, msg

    return send_template_sms(booking, "payment_confirmation")


def maybe_send_thank_you(
    booking: Dict[str, Any],
    previous_status: str,
) -> Optional[str]:
    """Send thank-you SMS when status changes to Completed."""
    current = job_status.display(booking)
    if current != "Completed":
        return None
    if job_status.normalize(previous_status) == "Completed":
        return None
    ok, msg = send_template_sms(booking, "thank_you")
    return msg if ok or "disabled" not in msg.lower() else None


def maybe_send_booking_confirmed(
    booking: Dict[str, Any],
    previous_status: str,
) -> Optional[str]:
    """Send confirmation SMS when status changes from Pending to Confirmed."""
    current = job_status.display(booking)
    if current != "Confirmed":
        return None
    if job_status.normalize(previous_status) != "Pending":
        return None
    ok, msg = send_template_sms(booking, "booking_confirmed")
    return msg if ok or "disabled" not in msg.lower() else None


def _eligible_for_reminder(booking: Dict[str, Any]) -> bool:
    status = job_status.display(booking)
    if status == "Cancelled":
        return False
    return not _already_sent(booking, "booking_reminder")


def run_move_reminders(today: Optional[date] = None) -> List[str]:
    """Send booking reminders for moves tomorrow."""
    today = today or date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    messages = []
    for row in db.list_by_date(tomorrow):
        booking = dict(row)
        if not _eligible_for_reminder(booking):
            continue
        ok, msg = send_template_sms(booking, "booking_reminder")
        if ok or "already sent" not in msg.lower():
            messages.append("#{0}: {1}".format(booking["id"], msg))
    return messages


def run_payment_reminders(today: Optional[date] = None) -> List[str]:
    """Send payment reminders for overdue invoices."""
    today = today or date.today()
    messages = []
    for row in db.list_all():
        booking = dict(row)
        if not has_invoice(booking):
            continue
        if not _is_overdue(booking, today):
            continue
        if _already_sent(booking, "payment_reminder"):
            continue
        if job_status.display(booking) == "Cancelled":
            continue
        ok, msg = send_template_sms(booking, "payment_reminder")
        if ok or "already sent" not in msg.lower():
            messages.append("#{0}: {1}".format(booking["id"], msg))
    return messages


def run_scheduled_automations(today: Optional[date] = None) -> Dict[str, List[str]]:
    """Cron entrypoint — move reminders + overdue payment reminders."""
    if not sms_config.is_automation_enabled() or not sms.is_configured():
        return {"move_reminders": [], "payment_reminders": []}
    return {
        "move_reminders": run_move_reminders(today),
        "payment_reminders": run_payment_reminders(today),
    }


def sample_booking_for_test() -> Dict[str, Any]:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    due = (date.today() - timedelta(days=3)).isoformat()
    return {
        "id": 0,
        "customer_name": "Test Customer",
        "phone": "",
        "email": "test@example.com",
        "pickup_address": "123 Test St, Perth",
        "delivery_address": "456 Demo Ave, Fremantle",
        "move_date": tomorrow,
        "num_movers": 2,
        "notes": "",
        "hourly_rate": config.INVOICE_DEFAULT_HOURLY_RATE,
        "callout_fee": config.INVOICE_DEFAULT_CALLOUT_FEE,
        "gst_enabled": 1 if config.INVOICE_GST_ENABLED_DEFAULT else 0,
        "duration_hours": "4",
        "invoice_due_date": due,
    }
