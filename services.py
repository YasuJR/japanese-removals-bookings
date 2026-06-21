"""Run integrations after booking changes."""

from typing import Any, Dict, List, Optional, Tuple

import config
import database as db
import invoice
import job_status
from integrations import (
    confirmed_automation,
    email_send,
    gmail_inbox,
    google_calendar,
    on_route_automation,
    payment_reminder_automation,
    review_automation,
    sms,
    sms_automation,
    sms_config,
    xero,
    xero_config,
    xero_automation,
)
from integrations import stripe as stripe_service
import booking_profit


def booking_to_dict(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    booking = dict(row)
    booking_id = booking.get("id")
    if booking_id:
        booking["extra_charges"] = db.list_extra_charges(int(booking_id))
    else:
        booking["extra_charges"] = []
    return booking


def _persist_booking_extras(booking_id: int, data: Dict[str, Any]) -> None:
    db.replace_extra_charges(booking_id, data.get("extra_charges") or [])
    db.update_booking_invoice_fields(
        booking_id,
        {
            "invoice_custom_text": data.get("invoice_custom_text", ""),
            "invoice_bank_account_name": data.get("invoice_bank_account_name", ""),
            "invoice_bank_bsb": data.get("invoice_bank_bsb", ""),
            "invoice_bank_account": data.get("invoice_bank_account", ""),
        },
    )
    truck = (data.get("truck_assigned") or "").strip()
    if "truck_assigned" in data:
        db.update_booking_integration_fields(
            booking_id, {"truck_assigned": truck}
        )


def after_booking_created(booking_id: int) -> List[str]:
    """Calendar + optional SMS on new booking."""
    messages = []
    row = db.get_booking(booking_id)
    if not row:
        return messages
    booking = booking_to_dict(row)

    if job_status.display(booking) == "Pending":
        return messages

    cal_msg = google_calendar.sync_booking_to_calendar(booking)
    if cal_msg:
        messages.append(cal_msg)

    if sms_config.is_automation_enabled() and config.SMS_ON_BOOKING_CREATE:
        sms_msg = sms.send_booking_notification(booking, event="created")
        if sms_msg:
            messages.append(sms_msg)

    auto_msg = _auto_create_xero_invoice_on_create(booking_id)
    if auto_msg:
        messages.append(auto_msg)

    booking_profit.recalculate_and_save(booking_id)

    return messages


def _auto_create_xero_invoice_on_create(booking_id: int) -> Optional[str]:
    """Create and approve a Xero invoice when a new booking is saved."""
    if not xero_config.auto_create_on_booking_create():
        return None
    if not xero.is_ready():
        return None
    row = db.get_booking(booking_id)
    if not row:
        return None
    booking = booking_to_dict(row)
    if xero.is_real_invoice_id(booking.get("xero_invoice_id")):
        return None
    ok, msg, inv = xero.create_and_authorise_invoice_for_booking(booking)
    if not ok:
        return "Xero auto-create skipped: {0}".format(msg)
    if inv and inv.get("InvoiceNumber"):
        return "Xero invoice {0} created automatically.".format(inv.get("InvoiceNumber"))
    return msg or "Xero invoice created automatically."


def _maybe_auto_create_xero_draft(booking_id: int) -> Optional[str]:
    """Create a Xero draft when setting is on and booking is Confirmed."""
    if not xero_config.auto_create_draft_on_confirmed():
        return None
    if not xero.is_ready():
        return None
    row = db.get_booking(booking_id)
    if not row:
        return None
    booking = booking_to_dict(row)
    if job_status.display(booking) != "Confirmed":
        return None
    if xero.is_real_invoice_id(booking.get("xero_invoice_id")):
        return None
    ok, msg, _inv = xero.sync_invoice_record(booking, confirm_new=False)
    if ok:
        return msg
    return "Xero auto-create skipped: {0}".format(msg)


def after_booking_updated(
    booking_id: int,
    previous_status: Optional[str] = None,
) -> List[str]:
    messages = []
    row = db.get_booking(booking_id)
    if not row:
        return messages
    booking = booking_to_dict(row)
    pending_to_confirmed = confirmed_automation.is_pending_to_confirmed(
        booking, previous_status
    )

    if pending_to_confirmed:
        messages.extend(confirmed_automation.run_on_pending_to_confirmed(booking))
    elif job_status.display(booking) != "Pending":
        cal_msg = google_calendar.sync_booking_to_calendar(booking)
        if cal_msg:
            messages.append(cal_msg)

    if sms_config.is_automation_enabled() and config.SMS_ON_BOOKING_UPDATE:
        sms_msg = sms.send_booking_notification(booking, event="updated")
        if sms_msg:
            messages.append(sms_msg)

    if previous_status is not None:
        xero_msg = xero_automation.auto_create_invoice_on_pending_confirmed(
            booking, previous_status
        )
        if xero_msg:
            messages.append(xero_msg)
        thank_msg = sms_automation.maybe_send_thank_you(booking, previous_status)
        if thank_msg:
            messages.append(thank_msg)
        review_msg = review_automation.schedule_on_completed(
            booking, previous_status
        )
        if review_msg:
            messages.append(review_msg)

    xero_msg = sync_xero_draft_if_linked(booking_id)
    if xero_msg:
        messages.append(xero_msg)

    auto_msg = _maybe_auto_create_xero_draft(booking_id)
    if auto_msg:
        messages.append(auto_msg)

    booking_profit.recalculate_and_save(booking_id)

    return messages


def after_booking_deleted(booking: Dict[str, Any]) -> List[str]:
    messages = []
    cal_msg = google_calendar.delete_calendar_event(booking)
    if cal_msg:
        messages.append(cal_msg)
    return messages


def send_sms_manual(booking_id: int) -> Tuple[bool, str]:
    """Legacy manual SMS — uses booking confirmation template."""
    return send_booking_template_sms(booking_id, "booking_confirmation")


def send_booking_template_sms(
    booking_id: int,
    template_key: str,
) -> Tuple[bool, str]:
    if template_key not in sms_config.MANUAL_TEMPLATE_KEYS:
        return False, "Invalid SMS template."
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    if not sms.is_configured():
        return False, "SMS not configured — add Twilio settings to .env"
    return sms_automation.send_template_sms(
        booking_to_dict(row),
        template_key,
        force=True,
    )


def check_xero_payment_status(booking_id: int) -> Tuple[bool, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    return xero.sync_payment_status_from_xero(booking_to_dict(row))


def create_stripe_checkout(
    booking_id: int,
    *,
    success_url: str,
    cancel_url: str,
) -> Tuple[bool, str, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found.", ""
    return stripe_service.create_checkout_session(
        booking_to_dict(row),
        success_url=success_url,
        cancel_url=cancel_url,
        require_email=True,
    )


def prepare_booking_payment_link(booking_id: int) -> str:
    db.ensure_payment_token(booking_id)
    row = db.get_booking(booking_id)
    if not row:
        return ""
    return stripe_service.customer_payment_url(booking_to_dict(row))


def start_public_stripe_checkout(
    token: str,
    *,
    success_url: str,
    cancel_url: str,
) -> Tuple[bool, str, str]:
    row = db.get_booking_by_payment_token(token)
    if not row:
        return False, "Payment link not found.", ""
    booking = booking_to_dict(row)
    if (booking.get("payment_status") or "").strip() == invoice.PAYMENT_STATUS_PAID:
        return False, "This invoice is already paid.", ""
    return stripe_service.start_customer_checkout(
        booking,
        success_url=success_url,
        cancel_url=cancel_url,
    )


def handle_stripe_webhook(payload: bytes, signature: str) -> Tuple[bool, str]:
    return stripe_service.handle_webhook_event(payload, signature)


def run_scheduled_sms_automations() -> Dict[str, List[str]]:
    results = sms_automation.run_scheduled_automations()
    results["unpaid_invoice_reminders"] = (
        payment_reminder_automation.process_due_reminders()
    )
    return results


def send_payment_reminder_now(booking_id: int) -> Tuple[bool, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    return payment_reminder_automation.send_next_reminder_now(dict(row))


def cancel_payment_reminders(booking_id: int) -> Tuple[bool, str]:
    return payment_reminder_automation.cancel_reminders(booking_id)


def mark_booking_completed(booking_id: int) -> Tuple[bool, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    booking = dict(row)
    previous_status = job_status.display(booking)
    if previous_status == "Completed":
        return True, "Booking already marked Completed."
    if previous_status not in ("Confirmed", "Paid", "On Route", "In Progress"):
        return False, "Cannot mark this booking Completed from its current status."
    db.update_booking_status(booking_id, "Completed")
    after_booking_updated(booking_id, previous_status=previous_status)
    return True, "Booking marked Completed."


def send_sms_test(template_key: str, test_phone: str) -> Tuple[bool, str]:
    if not (test_phone or "").strip():
        return False, "Enter a phone number for the test SMS."
    sample = sms_automation.sample_booking_for_test()
    sample["phone"] = test_phone.strip()
    return sms_automation.send_template_sms(
        sample,
        template_key,
        force=True,
        override_phone=test_phone.strip(),
    )


def create_xero_draft(booking_id: int, confirm_new: bool = False) -> Tuple[bool, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    booking = booking_to_dict(row)
    ok, msg, _inv = xero.sync_invoice_record(
        booking, confirm_new=confirm_new
    )
    return ok, msg


def sync_xero_draft_if_linked(booking_id: int) -> Optional[str]:
    """Update linked Xero draft after booking save (silent if not applicable)."""
    if not xero.is_ready():
        return None
    row = db.get_booking(booking_id)
    if not row:
        return None
    booking = booking_to_dict(row)
    if not xero.is_real_invoice_id(booking.get("xero_invoice_id")):
        return None
    if not xero.is_draft_invoice(booking):
        return None
    ok, msg, _inv = xero.sync_invoice_record(booking, confirm_new=False)
    if ok:
        return msg
    return "Xero invoice not updated: {0}".format(msg)


def run_xero_invoice_automation(booking_id: int) -> Tuple[bool, str, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found.", ""
    return xero_automation.create_approve_and_email_invoice(booking_to_dict(row))


def run_scheduled_review_automations() -> List[str]:
    return review_automation.process_due_requests()


def run_gmail_inbox_monitor() -> List[str]:
    return gmail_inbox.poll_inbox()


def send_review_test(channel: str, test_phone: str, test_email: str) -> Tuple[bool, str]:
    import secrets

    sample = review_automation.sample_booking()
    token = secrets.token_urlsafe(12)
    if channel == "sms":
        if not test_phone.strip():
            return False, "Enter a phone number for the test."
        sample["phone"] = test_phone.strip()
        body = review_automation.render_sms(sample, token)
        ok, msg, _sid = sms.send_message(
            sample,
            body,
            automation_type="google_review_test",
            template_key="google_review",
            status_callback=False,
        )
        return ok, msg
    if not test_email.strip():
        return False, "Enter an email address for the test."
    sample["email"] = test_email.strip()
    subject, body = review_automation.render_email(sample, token)
    return email_send.send_email(test_email.strip(), subject, body)


def mark_review_received(booking_id: int) -> Tuple[bool, str]:
    return review_automation.mark_reviewed_for_booking(booking_id)


def send_review_request_now(booking_id: int) -> Tuple[bool, str]:
    return review_automation.send_review_request_now(booking_id)


def cancel_review_request(booking_id: int) -> Tuple[bool, str]:
    return review_automation.cancel_review_request_for_booking(booking_id)


def mark_booking_paid(booking_id: int, paid: bool) -> Tuple[bool, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    ok, msg = invoice.set_payment_status(booking_id, paid)
    if ok:
        booking_profit.recalculate_and_save(booking_id)
    return ok, msg


def save_profit_costs(booking_id: int, form: Dict[str, Any]) -> Tuple[bool, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    booking_profit.save_profit_cost_fields(booking_id, form)
    booking_profit.recalculate_and_save(booking_id)
    return True, "Profit costs saved and profit recalculated."


def recalculate_booking_profit(booking_id: int) -> Tuple[bool, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    metrics = booking_profit.recalculate_and_save(booking_id)
    if not metrics:
        return False, "Could not recalculate profit."
    return True, "Profit recalculated."


def start_driver_on_route(
    booking_id: int,
    *,
    driver_name: str,
    manual_eta_minutes=None,
    driver_origin: str = "",
) -> Tuple[bool, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    return on_route_automation.start_on_route(
        booking_to_dict(row),
        driver_name=driver_name,
        manual_eta_minutes=manual_eta_minutes,
        driver_origin=driver_origin,
    )


def resend_eta_sms(
    booking_id: int,
    *,
    driver_name: str = "",
    manual_eta_minutes=None,
    driver_origin: str = "",
) -> Tuple[bool, str]:
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    return on_route_automation.resend_eta_sms(
        booking_to_dict(row),
        driver_name=driver_name,
        manual_eta_minutes=manual_eta_minutes,
        driver_origin=driver_origin,
    )
