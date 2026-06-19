"""Phase 9 — Pending → Confirmed automation (SMS, calendar, staff notify)."""

from datetime import datetime
from typing import Any, Dict, List, Optional

import automation
import config
import database as db
import job_status
from integrations import email_send, gmail_config, google_calendar, sms_automation, sms_config


def is_pending_to_confirmed(
    booking: Dict[str, Any],
    previous_status: Optional[str],
) -> bool:
    if previous_status is None:
        return False
    return (
        job_status.display(booking) == "Confirmed"
        and job_status.normalize(previous_status) == "Pending"
    )


def _booking_edit_url(booking_id: int) -> str:
    base = (config.APP_BASE_URL or "").rstrip("/")
    return "{0}/bookings/{1}/edit".format(base, booking_id)


def run_on_pending_to_confirmed(booking: Dict[str, Any]) -> List[str]:
    """Run confirmation SMS, calendar sync, and staff notification once each."""
    messages: List[str] = []
    sms_msg = _maybe_send_confirmation_sms(booking)
    if sms_msg:
        messages.append(sms_msg)

    cal_msg = _maybe_sync_calendar(booking)
    if cal_msg:
        messages.append(cal_msg)

    staff_msg = _maybe_notify_staff(booking)
    if staff_msg:
        messages.append(staff_msg)

    return messages


def _maybe_send_confirmation_sms(booking: Dict[str, Any]) -> Optional[str]:
    booking_id = int(booking["id"])
    if (booking.get("sms_booking_confirmed_sent_at") or "").strip():
        msg = "Confirmation SMS already sent."
        automation.log_event(
            automation.AUTOMATION_CONFIRMATION_SMS_SENT,
            automation.STATUS_PARTIAL,
            msg,
            booking_id,
        )
        return msg

    if not sms_config.is_trigger_enabled("booking_confirmed"):
        msg = "Booking confirmed SMS disabled in Settings → SMS."
        automation.log_event(
            automation.AUTOMATION_CONFIRMATION_SMS_SENT,
            automation.STATUS_PARTIAL,
            msg,
            booking_id,
        )
        return msg

    ok, msg = sms_automation.send_template_sms(
        booking,
        "booking_confirmed",
        automation_log_type=automation.AUTOMATION_CONFIRMATION_SMS_SENT,
    )
    return msg if ok or "disabled" not in msg.lower() else None


def _maybe_sync_calendar(booking: Dict[str, Any]) -> Optional[str]:
    booking_id = int(booking["id"])
    if (booking.get("calendar_confirmed_synced_at") or "").strip():
        msg = "Calendar already synced on confirm."
        automation.log_event(
            automation.AUTOMATION_CALENDAR_EVENT_SYNCED,
            automation.STATUS_PARTIAL,
            msg,
            booking_id,
        )
        return msg

    cal_msg = google_calendar.sync_booking_to_calendar(booking)
    if not cal_msg:
        return None

    lowered = cal_msg.lower()
    if "failed" in lowered or "not connected" in lowered or "expired" in lowered:
        automation.log_event(
            automation.AUTOMATION_CALENDAR_EVENT_SYNCED,
            automation.STATUS_ERROR,
            cal_msg,
            booking_id,
        )
        return cal_msg

    db.update_booking_integration_fields(
        booking_id,
        {
            "calendar_confirmed_synced_at": datetime.utcnow().isoformat(
                timespec="seconds"
            )
        },
    )
    automation.log_event(
        automation.AUTOMATION_CALENDAR_EVENT_SYNCED,
        automation.STATUS_SUCCESS,
        cal_msg,
        booking_id,
    )
    return cal_msg


def _maybe_notify_staff(booking: Dict[str, Any]) -> Optional[str]:
    booking_id = int(booking["id"])
    if (booking.get("staff_notification_sent_at") or "").strip():
        msg = "Staff notification already sent."
        automation.log_event(
            automation.AUTOMATION_STAFF_NOTIFICATION_SENT,
            automation.STATUS_PARTIAL,
            msg,
            booking_id,
        )
        return msg

    admin_email = gmail_config.admin_notify_email()
    if not admin_email:
        msg = "No admin notify email configured."
        automation.log_event(
            automation.AUTOMATION_STAFF_NOTIFICATION_SENT,
            automation.STATUS_PARTIAL,
            msg,
            booking_id,
        )
        return msg

    edit_url = _booking_edit_url(booking_id)
    subject = "Booking confirmed — #{0}".format(booking_id)
    body = (
        "A booking has been confirmed.\n\n"
        "Customer: {customer_name}\n"
        "Phone: {phone}\n"
        "Email: {email}\n"
        "Move date: {move_date}\n"
        "Start time: {start_time}\n"
        "Pickup: {pickup_address}\n"
        "Delivery: {delivery_address}\n\n"
        "View booking:\n{edit_url}\n"
    ).format(
        customer_name=(booking.get("customer_name") or "").strip() or "—",
        phone=(booking.get("phone") or "").strip() or "—",
        email=(booking.get("email") or "").strip() or "—",
        move_date=(booking.get("move_date") or "").strip() or "—",
        start_time=(booking.get("start_time") or "").strip() or "08:00",
        pickup_address=(booking.get("pickup_address") or "").strip() or "—",
        delivery_address=(booking.get("delivery_address") or "").strip() or "—",
        edit_url=edit_url,
    )
    ok, msg = email_send.send_email(admin_email, subject, body)
    if ok:
        db.update_booking_integration_fields(
            booking_id,
            {
                "staff_notification_sent_at": datetime.utcnow().isoformat(
                    timespec="seconds"
                )
            },
        )
        automation.log_event(
            automation.AUTOMATION_STAFF_NOTIFICATION_SENT,
            automation.STATUS_SUCCESS,
            msg,
            booking_id,
        )
    else:
        automation.log_event(
            automation.AUTOMATION_STAFF_NOTIFICATION_SENT,
            automation.STATUS_ERROR,
            msg,
            booking_id,
        )
    return msg
