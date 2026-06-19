"""
Customer SMS via Twilio.

Setup:
1. Create account at https://www.twilio.com/
2. Buy an Australian mobile number (or use trial + verified numbers).
3. Add to .env: SMS_ENABLED=true, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
4. Optional: SMS_ON_BOOKING_CREATE=true, SMS_ON_BOOKING_UPDATE=true
"""

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import automation
import config
import database as db


def is_configured() -> bool:
    return bool(
        config.SMS_ENABLED
        and config.TWILIO_ACCOUNT_SID
        and config.TWILIO_AUTH_TOKEN
        and config.TWILIO_FROM_NUMBER
    )


def _normalize_phone(phone: str) -> str:
    """Convert Australian numbers to E.164 (+61...)."""
    raw = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    if raw.startswith("+"):
        return raw
    if raw.startswith("0"):
        return "+61" + raw[1:]
    if raw.startswith("61"):
        return "+" + raw
    return "+61" + raw


def _message_body(booking: Dict[str, Any], event: str) -> str:
    company = config.COMPANY_NAME
    if event == "created":
        intro = "{0}: Your move is booked.".format(company)
    elif event == "updated":
        intro = "{0}: Your move details were updated.".format(company)
    else:
        intro = "{0}: Move reminder.".format(company)

    parts = [
        intro,
        "Ref #{0}.".format(booking["id"]),
        "Date: {0}.".format(booking["move_date"]),
        "Pickup: {0}.".format(booking["pickup_address"]),
        "Delivery: {0}.".format(booking["delivery_address"]),
    ]
    if config.COMPANY_PHONE:
        parts.append("Questions? Call {0}.".format(config.COMPANY_PHONE))
    return " ".join(parts)


def status_callback_url() -> str:
    """Public URL for Twilio delivery status webhooks (set in app context)."""
    try:
        from flask import url_for

        return url_for("twilio_status_callback", _external=True)
    except RuntimeError:
        return ""


def _map_twilio_status(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in ("delivered",):
        return automation.STATUS_DELIVERED
    if value in ("failed", "undelivered"):
        return automation.STATUS_FAILED
    if value in ("sent", "queued", "accepted", "sending"):
        return automation.STATUS_SENT
    return value or automation.STATUS_SENT


def update_delivery_status(
    twilio_sid: str,
    raw_status: str,
    error_message: str = "",
) -> bool:
    """Update delivery log and automation log when Twilio reports status."""
    mapped = _map_twilio_status(raw_status)
    updated = db.update_sms_delivery_by_sid(twilio_sid, mapped, error_message)
    if not updated:
        return False

    if mapped == automation.STATUS_DELIVERED:
        automation.log_event(
            "sms_delivery",
            automation.STATUS_DELIVERED,
            "Delivered ({0})".format(twilio_sid[:8]),
        )
    elif mapped == automation.STATUS_FAILED:
        automation.log_event(
            "sms_delivery",
            automation.STATUS_FAILED,
            "Failed: {0}".format(error_message or raw_status),
        )
    return True


def send_message(
    booking: Dict[str, Any],
    body: str,
    *,
    automation_type: str = "",
    template_key: str = "",
    status_callback: bool = True,
) -> Tuple[bool, str, Optional[str]]:
    """
    Send SMS with delivery tracking. Returns (ok, message, twilio_sid).
    """
    if not is_configured():
        return False, "SMS not configured — add Twilio settings to .env", None

    if not (booking.get("phone") or "").strip():
        return False, "SMS skipped — no phone number on this booking.", None

    try:
        from twilio.rest import Client
    except ImportError:
        return False, "Twilio library not installed. Run: pip install twilio", None

    to_number = _normalize_phone(booking["phone"])
    booking_id = int(booking["id"]) if booking.get("id") else None

    try:
        client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        kwargs = {
            "body": body,
            "from_": config.TWILIO_FROM_NUMBER,
            "to": to_number,
        }
        callback = status_callback_url() if status_callback else ""
        if callback:
            kwargs["status_callback"] = callback

        message = client.messages.create(**kwargs)
        sid = message.sid or ""
        initial_status = _map_twilio_status(getattr(message, "status", "queued"))

        db.add_sms_delivery_log(
            booking_id=booking_id,
            automation_type=automation_type,
            template_key=template_key,
            twilio_sid=sid,
            to_number=to_number,
            body=body,
            status=initial_status,
        )
        if booking_id:
            db.update_booking_integration_fields(
                booking_id,
                {"sms_last_sent_at": datetime.utcnow().isoformat(timespec="seconds")},
            )
        return True, "SMS sent to {0}.".format(to_number), sid
    except Exception as exc:
        db.add_sms_delivery_log(
            booking_id=booking_id,
            automation_type=automation_type,
            template_key=template_key,
            twilio_sid="",
            to_number=to_number,
            body=body,
            status=automation.STATUS_FAILED,
            error_message=str(exc),
        )
        return False, "SMS failed: {0}".format(exc), None


def send_booking_notification(
    booking: Dict[str, Any], event: str = "created"
) -> Optional[str]:
    """Send SMS; returns status message or None if skipped."""
    ok, msg, _sid = _send_legacy(booking, event)
    return msg if ok or msg else None


def send_booking_notification_result(
    booking: Dict[str, Any], event: str = "manual"
) -> Tuple[bool, str]:
    ok, msg, _sid = _send_legacy(booking, event)
    return ok, msg


def _send_legacy(booking: Dict[str, Any], event: str) -> Tuple[bool, str, Optional[str]]:
    body = _message_body(booking, event)
    return send_message(
        booking,
        body,
        automation_type="sms_manual" if event == "manual" else "sms_{0}".format(event),
        template_key=event,
    )
