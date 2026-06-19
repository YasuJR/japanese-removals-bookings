"""Phase 16 — unpaid invoice payment reminder automation (3, 7, 14 days)."""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import automation
import config
import database as db
import invoice
import job_status
from integrations import sms, sms_config, xero
from outstanding_invoices_data import (
    _is_paid,
    has_invoice,
    invoice_number_display,
    issue_date_for,
)

REMINDER_THRESHOLDS = (3, 7, 14)
SENT_FIELDS = (
    "payment_reminder_1_sent_at",
    "payment_reminder_2_sent_at",
    "payment_reminder_3_sent_at",
)
LOG_TYPES = (
    automation.AUTOMATION_PAYMENT_REMINDER_1_SENT,
    automation.AUTOMATION_PAYMENT_REMINDER_2_SENT,
    automation.AUTOMATION_PAYMENT_REMINDER_3_SENT,
)

DEFAULT_SMS_TEMPLATE = (
    "Hi {first_name},\n"
    "Just a friendly reminder that payment for your Japanese Removals invoice "
    "{invoice_number} is still outstanding.\n"
    "Amount: {amount_due}\n"
    "You can pay by bank transfer or credit card using the invoice link.\n"
    "Thank you,\n"
    "Yasu\n"
    "Japanese Removals"
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_iso_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


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


def _amount_due(booking: Dict[str, Any]) -> str:
    totals = invoice.calculate_invoice_totals(
        invoice.resolve_booking_invoice(booking)
    )
    return invoice.format_aud(totals["total"])


def days_since_invoice(booking: Dict[str, Any], today: date) -> Optional[int]:
    issue = _parse_iso_date(issue_date_for(booking))
    if issue is None:
        return None
    return (today - issue).days


def has_authorised_invoice(booking: Dict[str, Any]) -> bool:
    """Invoice exists and is not a draft."""
    if not has_invoice(booking):
        return False
    if xero.is_real_invoice_id(booking.get("xero_invoice_id")):
        return not xero.is_draft_invoice(booking)
    status = (booking.get("invoice_status") or "").strip().upper()
    if status in ("DRAFT", "DRAFT CREATED"):
        return False
    return bool((booking.get("invoice_number") or "").strip()) or status in (
        "AUTHORISED",
        "SUBMITTED",
        "PAID",
    )


def is_reminders_cancelled(booking: Dict[str, Any]) -> bool:
    return bool((booking.get("payment_reminders_cancelled_at") or "").strip())


def is_eligible(booking: Dict[str, Any]) -> bool:
    status = job_status.display(booking)
    if status in ("Pending", "Cancelled"):
        return False
    if _is_paid(booking):
        return False
    if is_reminders_cancelled(booking):
        return False
    if not has_authorised_invoice(booking):
        return False
    return True


def can_send_manual(booking: Dict[str, Any]) -> bool:
    if not is_eligible(booking):
        return False
    return next_unsent_level(booking) is not None


def can_cancel(booking: Dict[str, Any]) -> bool:
    if is_reminders_cancelled(booking):
        return False
    if _is_paid(booking) or job_status.display(booking) == "Cancelled":
        return False
    if not has_authorised_invoice(booking):
        return False
    return any((booking.get(field) or "").strip() for field in SENT_FIELDS) or True


def next_unsent_level(booking: Dict[str, Any]) -> Optional[int]:
    for index, field in enumerate(SENT_FIELDS, start=1):
        if not (booking.get(field) or "").strip():
            return index
    return None


def next_due_level(booking: Dict[str, Any], today: date) -> Optional[int]:
    days = days_since_invoice(booking, today)
    if days is None:
        return None
    for index, threshold in enumerate(REMINDER_THRESHOLDS, start=1):
        field = SENT_FIELDS[index - 1]
        if days >= threshold and not (booking.get(field) or "").strip():
            return index
    return None


def render_sms(booking: Dict[str, Any]) -> str:
    template = sms_config.get_template("unpaid_invoice_reminder") or DEFAULT_SMS_TEMPLATE
    values = {
        "customer_name": (booking.get("customer_name") or "").strip() or "Customer",
        "first_name": _first_name(booking),
        "invoice_number": invoice_number_display(booking),
        "amount_due": _amount_due(booking),
        "invoice_link": _invoice_link(booking),
        "company_name": config.COMPANY_NAME,
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise ValueError("Unknown placeholder in template: {0}".format(exc)) from exc


def badges_for_booking(booking: Dict[str, Any]) -> List[Dict[str, str]]:
    badges: List[Dict[str, str]] = []
    if is_reminders_cancelled(booking):
        badges.append(
            {
                "label": "Payment reminders cancelled",
                "css_class": "payment-reminder-cancelled",
            }
        )
        return badges
    labels = (
        "Payment reminder 1 sent",
        "Payment reminder 2 sent",
        "Payment reminder 3 sent",
    )
    for field, label in zip(SENT_FIELDS, labels):
        sent_at = (booking.get(field) or "").strip()
        if sent_at:
            badges.append(
                {
                    "label": label,
                    "css_class": "payment-reminder-sent",
                    "sent_at": sent_at[:16],
                }
            )
    return badges


def send_reminder(
    booking: Dict[str, Any],
    level: int,
    *,
    force: bool = False,
) -> Tuple[bool, str]:
    booking_id = int(booking["id"])
    if level < 1 or level > 3:
        return False, "Invalid reminder level."

    log_type = LOG_TYPES[level - 1]
    sent_field = SENT_FIELDS[level - 1]

    if not force and not is_eligible(booking):
        msg = "Booking not eligible for payment reminders."
        automation.log_event(log_type, automation.STATUS_PARTIAL, msg, booking_id)
        return False, msg

    if (booking.get(sent_field) or "").strip() and not force:
        msg = "Payment reminder {0} already sent.".format(level)
        automation.log_event(log_type, automation.STATUS_PARTIAL, msg, booking_id)
        return False, msg

    if not sms.is_configured():
        msg = "SMS not configured — add Twilio settings to .env"
        automation.log_event(log_type, automation.STATUS_ERROR, msg, booking_id)
        return False, msg

    if not force and not sms_config.is_automation_enabled():
        return False, "SMS automation disabled in Settings."

    if not force and not sms_config.is_trigger_enabled("unpaid_invoice_reminder"):
        return False, "Unpaid invoice reminders disabled in Settings."

    phone = (booking.get("phone") or "").strip()
    if not phone:
        msg = "No phone number on booking."
        automation.log_event(log_type, automation.STATUS_ERROR, msg, booking_id)
        return False, msg

    try:
        body = render_sms(booking)
    except ValueError as exc:
        automation.log_event(log_type, automation.STATUS_ERROR, str(exc), booking_id)
        return False, str(exc)

    ok, msg, _sid = sms.send_message(
        booking,
        body,
        automation_type=log_type,
        template_key="unpaid_invoice_reminder",
    )
    if not ok:
        automation.log_event(log_type, automation.STATUS_ERROR, msg, booking_id)
        return False, msg

    now = _iso(_utcnow())
    db.update_booking_integration_fields(booking_id, {sent_field: now})
    automation.log_event(
        log_type,
        automation.STATUS_SENT,
        "Payment reminder {0} sent for invoice {1}.".format(
            level, invoice_number_display(booking)
        ),
        booking_id,
    )
    return True, "Payment reminder {0} sent.".format(level)


def send_next_reminder_now(booking: Dict[str, Any]) -> Tuple[bool, str]:
    level = next_unsent_level(booking)
    if level is None:
        return False, "All payment reminders have already been sent."
    return send_reminder(booking, level, force=True)


def cancel_reminders(booking_id: int) -> Tuple[bool, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    booking = dict(row)
    if is_reminders_cancelled(booking):
        return True, "Payment reminders already cancelled."
    now = _iso(_utcnow())
    db.update_booking_integration_fields(
        booking_id,
        {"payment_reminders_cancelled_at": now},
    )
    automation.log_event(
        automation.AUTOMATION_PAYMENT_REMINDERS_CANCELLED,
        automation.STATUS_SUCCESS,
        "Payment reminders cancelled.",
        booking_id,
    )
    return True, "Payment reminders cancelled."


def process_due_reminders(today: Optional[date] = None) -> List[str]:
    """Cron entrypoint — send due 3/7/14-day unpaid invoice reminders."""
    today = today or date.today()
    messages: List[str] = []
    if not sms_config.is_automation_enabled() or not sms.is_configured():
        return messages
    if not sms_config.is_trigger_enabled("unpaid_invoice_reminder"):
        return messages

    for row in db.list_all():
        booking = dict(row)
        if not is_eligible(booking):
            continue
        level = next_due_level(booking, today)
        if level is None:
            continue
        ok, msg = send_reminder(booking, level)
        messages.append("#{0} reminder {1}: {2}".format(booking["id"], level, msg))
    return messages


def sample_booking_for_test() -> Dict[str, Any]:
    issue = (date.today() - timedelta(days=3)).isoformat()
    return {
        "id": 0,
        "customer_name": "Test Customer",
        "phone": "0412345678",
        "email": "test@example.com",
        "pickup_address": "123 Test St, Perth",
        "delivery_address": "456 Demo Ave, Fremantle",
        "move_date": date.today().isoformat(),
        "num_movers": 2,
        "notes": "",
        "hourly_rate": config.INVOICE_DEFAULT_HOURLY_RATE,
        "callout_fee": config.INVOICE_DEFAULT_CALLOUT_FEE,
        "gst_enabled": 1 if config.INVOICE_GST_ENABLED_DEFAULT else 0,
        "duration_hours": "3",
        "invoice_number": "INV-TEST-001",
        "invoice_status": "AUTHORISED",
        "invoice_issue_date": issue,
        "payment_status": invoice.PAYMENT_STATUS_UNPAID,
        "status": "Completed",
    }
