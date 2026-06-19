"""Google review request automation — schedule, send, track clicks."""

import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import automation
import config
import database as db
import job_status
from integrations import email_send, review_config, sms


def _utcnow() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def tracking_url(token: str) -> str:
    base = (config.APP_BASE_URL or "").rstrip("/")
    return "{0}/review/go/{1}".format(base, token)


def confirm_url(token: str) -> str:
    base = (config.APP_BASE_URL or "").rstrip("/")
    return "{0}/review/done/{1}".format(base, token)


def _first_name(booking: Dict[str, Any]) -> str:
    name = (booking.get("customer_name") or "").strip()
    if not name:
        return "Customer"
    return name.split()[0]


def _template_values(booking: Dict[str, Any], token: str) -> Dict[str, str]:
    google_url = review_config.get_google_review_url() or tracking_url(token)
    return {
        "customer_name": (booking.get("customer_name") or "").strip() or "Customer",
        "first_name": _first_name(booking),
        "company_name": config.COMPANY_NAME,
        "review_link": tracking_url(token),
        "review_confirm_link": confirm_url(token),
        "google_review_link": google_url,
    }


def render_sms(booking: Dict[str, Any], token: str) -> str:
    template = review_config.get_sms_template()
    return template.format(**_template_values(booking, token))


def render_email(booking: Dict[str, Any], token: str) -> Tuple[str, str]:
    values = _template_values(booking, token)
    subject = review_config.get_email_subject().format(**values)
    body = review_config.get_email_body().format(**values)
    return subject, body


def _resolve_channel(booking: Dict[str, Any]) -> Optional[str]:
    """SMS first; email fallback when phone is missing."""
    channel = review_config.get_channel()
    has_phone = bool((booking.get("phone") or "").strip())
    has_email = bool((booking.get("email") or "").strip())

    if channel == review_config.CHANNEL_SMS:
        return "sms" if has_phone else None
    if channel == review_config.CHANNEL_EMAIL:
        return "email" if has_email else None
    if has_phone:
        return "sms"
    if has_email:
        return "email"
    return None


def _mark_completed(booking_id: int, booking: Dict[str, Any]) -> None:
    now = _iso(_utcnow())
    fields: Dict[str, Any] = {}
    if not (booking.get("completed_at") or "").strip():
        fields["completed_at"] = now
    if fields:
        db.update_booking_on_route_fields(booking_id, fields)


def schedule_on_completed(
    booking: Dict[str, Any],
    previous_status: str,
) -> Optional[str]:
    """Queue a review request after wait_hours when status changes to Completed."""
    if not review_config.is_automation_enabled():
        return None

    current = job_status.display(booking)
    if current != "Completed":
        return None
    if job_status.normalize(previous_status) == "Completed":
        return None

    booking_id = int(booking["id"])
    if db.get_active_review_request_for_booking(booking_id):
        automation.log_event(
            automation.AUTOMATION_REVIEW_REQUEST_SCHEDULED,
            automation.STATUS_PARTIAL,
            "Review request already scheduled.",
            booking_id,
        )
        return None

    if (booking.get("review_request_sent_at") or "").strip():
        automation.log_event(
            automation.AUTOMATION_REVIEW_REQUEST_SCHEDULED,
            automation.STATUS_PARTIAL,
            "Review request already sent.",
            booking_id,
        )
        return None

    wait_hours = review_config.get_wait_hours()
    scheduled_at = _utcnow() + timedelta(hours=wait_hours)
    token = secrets.token_urlsafe(16)
    channel = _resolve_channel(booking) or review_config.get_channel()

    db.create_review_request(
        booking_id=booking_id,
        token=token,
        channel=channel,
        scheduled_at=_iso(scheduled_at),
    )
    _mark_completed(booking_id, booking)
    db.update_booking_on_route_fields(
        booking_id,
        {
            "review_request_scheduled_at": _iso(scheduled_at),
            "review_request_cancelled_at": "",
        },
    )
    automation.log_event(
        automation.AUTOMATION_REVIEW_REQUEST_SCHEDULED,
        automation.STATUS_SCHEDULED,
        "Review request scheduled for {0} ({1}h wait, {2}).".format(
            scheduled_at.strftime("%Y-%m-%d %H:%M UTC"),
            wait_hours,
            channel,
        ),
        booking_id=booking_id,
    )
    return "Google review request scheduled ({0}h after completion).".format(
        wait_hours
    )


def send_review_request(request_row: Dict[str, Any]) -> Tuple[bool, str]:
    """Send a due review request. Returns (ok, message)."""
    request_id = int(request_row["id"])
    booking_id = int(request_row["booking_id"])
    token = request_row["token"]

    if request_row.get("status") != automation.STATUS_SCHEDULED:
        msg = "Review request is not scheduled."
        automation.log_event(
            automation.AUTOMATION_REVIEW_REQUEST_SENT,
            automation.STATUS_PARTIAL,
            msg,
            booking_id,
        )
        return False, msg

    row = db.get_booking(booking_id)
    if not row:
        db.update_review_request_status(request_id, "failed", error="Booking not found")
        return False, "Booking not found."

    booking = dict(row)
    if (booking.get("review_request_sent_at") or "").strip():
        automation.log_event(
            automation.AUTOMATION_REVIEW_REQUEST_SENT,
            automation.STATUS_PARTIAL,
            "Review request already sent.",
            booking_id,
        )
        return False, "Review request already sent."

    channel = _resolve_channel(booking)
    if not channel:
        msg = "No phone or email on booking."
        db.update_review_request_status(request_id, "failed", error=msg)
        automation.log_event(
            automation.AUTOMATION_REVIEW_REQUEST_SENT,
            automation.STATUS_ERROR,
            msg,
            booking_id,
        )
        return False, msg

    if channel == "sms":
        if not sms.is_configured():
            msg = "SMS not configured."
            db.update_review_request_status(request_id, "failed", error=msg)
            automation.log_event(
                automation.AUTOMATION_REVIEW_REQUEST_SENT,
                automation.STATUS_ERROR,
                msg,
                booking_id,
            )
            return False, msg
        body = render_sms(booking, token)
        ok, msg, _sid = sms.send_message(
            booking,
            body,
            automation_type=automation.AUTOMATION_REVIEW_REQUEST_SENT,
            template_key="google_review",
            status_callback=False,
        )
    else:
        if not email_send.is_configured():
            msg = "Email not configured."
            db.update_review_request_status(request_id, "failed", error=msg)
            automation.log_event(
                automation.AUTOMATION_REVIEW_REQUEST_SENT,
                automation.STATUS_ERROR,
                msg,
                booking_id,
            )
            return False, msg
        subject, body = render_email(booking, token)
        ok, msg = email_send.send_email(booking["email"], subject, body)

    sent_at = _iso(_utcnow())
    if ok:
        db.update_review_request_status(
            request_id,
            automation.STATUS_SENT,
            channel=channel,
            sent_at=sent_at,
        )
        db.update_booking_on_route_fields(
            booking_id,
            {"review_request_sent_at": sent_at},
        )
        automation.log_event(
            automation.AUTOMATION_REVIEW_REQUEST_SENT,
            automation.STATUS_SENT,
            "{0} via {1}".format(msg, channel),
            booking_id=booking_id,
        )
    else:
        db.update_review_request_status(request_id, "failed", error=msg)
        automation.log_event(
            automation.AUTOMATION_REVIEW_REQUEST_SENT,
            automation.STATUS_ERROR,
            msg,
            booking_id=booking_id,
        )
    return ok, msg


def send_review_request_now(booking_id: int) -> Tuple[bool, str]:
    """Send a scheduled review request immediately."""
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    booking = dict(row)
    if job_status.display(booking) != "Completed":
        return False, "Booking must be Completed before sending a review request."

    active = db.get_active_review_request_for_booking(booking_id)
    if active:
        request = dict(active)
        if request.get("status") in (
            automation.STATUS_SENT,
            automation.STATUS_CLICKED,
            automation.STATUS_REVIEWED,
        ):
            automation.log_event(
                automation.AUTOMATION_REVIEW_REQUEST_SENT,
                automation.STATUS_PARTIAL,
                "Review request already sent.",
                booking_id,
            )
            return False, "Review request already sent."
        if request.get("status") == automation.STATUS_SCHEDULED:
            return send_review_request(request)

    if not review_config.is_automation_enabled():
        return False, "Google review automation is disabled or missing review URL."

    token = secrets.token_urlsafe(16)
    channel = _resolve_channel(booking) or review_config.get_channel()
    now = _iso(_utcnow())
    db.create_review_request(
        booking_id=booking_id,
        token=token,
        channel=channel,
        scheduled_at=now,
    )
    if not (booking.get("review_request_scheduled_at") or "").strip():
        db.update_booking_on_route_fields(
            booking_id,
            {"review_request_scheduled_at": now},
        )
    created = db.get_active_review_request_for_booking(booking_id)
    if not created:
        return False, "Could not create review request."
    return send_review_request(dict(created))


def cancel_review_request_for_booking(booking_id: int) -> Tuple[bool, str]:
    """Cancel a scheduled review request that has not been sent."""
    active = db.get_active_review_request_for_booking(booking_id)
    if not active:
        return False, "No active review request to cancel."
    request = dict(active)
    if request.get("status") != automation.STATUS_SCHEDULED:
        return False, "Only scheduled review requests can be cancelled."

    if not db.cancel_review_request(int(request["id"])):
        return False, "Review request could not be cancelled."

    cancelled_at = _iso(_utcnow())
    db.update_booking_on_route_fields(
        booking_id,
        {"review_request_cancelled_at": cancelled_at},
    )
    automation.log_event(
        automation.AUTOMATION_REVIEW_REQUEST_CANCELLED,
        automation.STATUS_SUCCESS,
        "Review request cancelled before send.",
        booking_id,
    )
    return True, "Review request cancelled."


def process_due_requests(now: Optional[datetime] = None) -> List[str]:
    """Send all scheduled review requests that are due."""
    if not review_config.is_automation_enabled():
        return []

    now = now or _utcnow()
    messages = []
    for row in db.list_due_review_requests(_iso(now)):
        ok, msg = send_review_request(dict(row))
        messages.append("#{0}: {1}".format(row["booking_id"], msg))
        if not ok and "not configured" in msg.lower():
            break
    return messages


def record_click(token: str) -> Optional[Dict[str, Any]]:
    """Mark review link clicked; returns request row or None."""
    row = db.get_review_request_by_token(token)
    if not row:
        return None
    request = dict(row)
    if request["status"] not in (
        automation.STATUS_SENT,
        automation.STATUS_CLICKED,
        automation.STATUS_REVIEWED,
    ):
        return None
    if request["status"] == automation.STATUS_SENT:
        db.mark_review_request_clicked(int(request["id"]), _iso(_utcnow()))
        automation.log_event(
            automation.AUTOMATION_GOOGLE_REVIEW,
            automation.STATUS_CLICKED,
            "Review link clicked.",
            booking_id=int(request["booking_id"]),
        )
    updated = db.get_review_request_by_token(token)
    return dict(updated) if updated else None


def mark_reviewed(
    token: str,
    *,
    by_staff: bool = False,
) -> Tuple[bool, str]:
    row = db.get_review_request_by_token(token)
    if not row:
        return False, "Review request not found."
    request = dict(row)
    if request["status"] == automation.STATUS_REVIEWED:
        return True, "Already marked as reviewed."

    db.mark_review_request_reviewed(int(request["id"]), _iso(_utcnow()))
    source = "staff" if by_staff else "customer"
    automation.log_event(
        automation.AUTOMATION_GOOGLE_REVIEW,
        automation.STATUS_REVIEWED,
        "Marked as reviewed ({0}).".format(source),
        booking_id=int(request["booking_id"]),
    )
    return True, "Marked as reviewed."


def mark_reviewed_for_booking(booking_id: int) -> Tuple[bool, str]:
    row = db.get_review_request_for_booking(booking_id)
    if not row:
        return False, "No review request for this booking."
    return mark_reviewed(row["token"], by_staff=True)


def sample_booking() -> Dict[str, Any]:
    return {
        "id": 0,
        "customer_name": "Test Customer",
        "phone": "",
        "email": "test@example.com",
        "pickup_address": "123 Test St",
        "delivery_address": "456 Demo Ave",
        "move_date": _utcnow().date().isoformat(),
        "num_movers": 2,
        "notes": "",
    }
