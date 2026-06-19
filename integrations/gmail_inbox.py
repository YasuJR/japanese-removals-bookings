"""Gmail inbox monitoring — create Pending bookings from new emails."""

from typing import Dict, List, Tuple

import automation
import config
import database as db
import job_status
from integrations import company_config, email_send, gmail_config, gmail_parser, google_oauth


def is_ready() -> bool:
    return (
        gmail_config.is_automation_enabled()
        and google_oauth.is_token_present()
        and google_oauth.gmail_scope_granted()
    )


def _gmail_service():
    from googleapiclient.discovery import build

    creds = google_oauth.get_credentials()
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _booking_edit_url(booking_id: int) -> str:
    base = (config.APP_BASE_URL or "").rstrip("/")
    return "{0}/bookings/{1}/edit".format(base, booking_id)


def _notify_admin(booking_id: int, fields: Dict[str, str]) -> Tuple[bool, str]:
    admin_email = gmail_config.admin_notify_email()
    if not admin_email:
        return False, "No admin notify email configured."

    edit_url = _booking_edit_url(booking_id)
    subject = "New booking from email — review required (#{0})".format(booking_id)
    body = (
        "A new booking was created automatically from a Gmail message.\n\n"
        "Status: Pending (please review and confirm details)\n"
        "Customer: {customer_name}\n"
        "Phone: {phone}\n"
        "Email: {email}\n"
        "Move date: {move_date}\n"
        "Pickup: {pickup_address}\n"
        "Delivery: {delivery_address}\n\n"
        "Review booking:\n{edit_url}\n"
    ).format(
        customer_name=fields.get("customer_name") or "—",
        phone=fields.get("phone") or "—",
        email=fields.get("email") or "—",
        move_date=fields.get("move_date") or "—",
        pickup_address=fields.get("pickup_address") or "—",
        delivery_address=fields.get("delivery_address") or "—",
        edit_url=edit_url,
    )
    return email_send.send_email(admin_email, subject, body)


def create_booking_from_email(
    message_id: str,
    fields: Dict[str, str],
) -> Tuple[bool, str, int]:
    """Create a Pending booking from parsed email fields."""
    defaults = company_config.booking_form_defaults()
    missing = gmail_parser.missing_required_fields(fields)
    notes = (fields.get("notes") or "").strip()
    if missing:
        notes = (
            "Auto-created from email — missing fields: {0}.\n\n{1}"
        ).format(", ".join(missing), notes).strip()

    move_date = (fields.get("move_date") or "").strip() or gmail_parser.default_move_date()
    pickup = (fields.get("pickup_address") or "").strip() or "TBC — see email notes"
    delivery = (fields.get("delivery_address") or "").strip() or "TBC — see email notes"
    customer_name = (fields.get("customer_name") or "").strip() or "Email enquiry"
    phone = (fields.get("phone") or "").strip() or defaults["phone"]
    email = (fields.get("email") or "").strip() or defaults["email"]

    booking_id = db.create_booking(
        customer_name=customer_name,
        phone=phone,
        email=email,
        pickup_address=pickup,
        delivery_address=delivery,
        move_date=move_date,
        num_movers=1,
        notes=notes,
        hourly_rate=float(defaults.get("hourly_rate") or 0),
        callout_fee=float(defaults.get("callout_fee") or 0),
        gst_enabled=1 if defaults.get("gst_enabled") else 0,
        status="Pending",
        gmail_message_id=message_id,
    )
    db.mark_gmail_message_processed(
        message_id,
        booking_id=booking_id,
        subject=fields.get("subject") or "",
    )
    return True, "Booking #{0} created from email.".format(booking_id), booking_id


def process_message(message: Dict[str, str], message_id: str) -> List[str]:
    """Process one Gmail message dict from the API."""
    results: List[str] = []
    if db.is_gmail_message_processed(message_id):
        return results

    fields = gmail_parser.parse_gmail_message(message)
    ok, msg, booking_id = create_booking_from_email(message_id, fields)
    if not ok:
        automation.log_event(
            automation.AUTOMATION_GMAIL_INBOX,
            automation.STATUS_ERROR,
            msg,
        )
        results.append(msg)
        return results

    automation.log_event(
        automation.AUTOMATION_GMAIL_INBOX,
        automation.STATUS_SUCCESS,
        msg,
        booking_id,
    )
    results.append(msg)

    notify_ok, notify_msg = _notify_admin(booking_id, fields)
    automation.log_event(
        automation.AUTOMATION_GMAIL_ADMIN_NOTIFY,
        automation.STATUS_SUCCESS if notify_ok else automation.STATUS_PARTIAL,
        notify_msg,
        booking_id,
    )
    results.append(notify_msg)
    return results


def poll_inbox(max_results: int = 20) -> List[str]:
    """Check Gmail inbox and create Pending bookings for new messages."""
    messages: List[str] = []

    if not gmail_config.is_automation_enabled():
        return ["Gmail inbox automation is disabled."]

    if not google_oauth.is_token_present():
        return ["Google token missing — connect Google in Settings."]

    if not google_oauth.gmail_scope_granted():
        return [
            "Gmail scope missing — open Settings and click Connect Google again "
            "to grant gmail.readonly."
        ]

    service = _gmail_service()
    if service is None:
        return ["Google login expired — reconnect in Settings."]

    query = gmail_config.inbox_query()
    try:
        listed = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
    except Exception as exc:
        automation.log_event(
            automation.AUTOMATION_GMAIL_INBOX,
            automation.STATUS_ERROR,
            "Gmail list failed: {0}".format(exc),
        )
        return ["Gmail list failed: {0}".format(exc)]

    refs = listed.get("messages") or []
    if not refs:
        gmail_config.touch_last_checked()
        return ["No matching Gmail messages."]

    for ref in refs:
        message_id = ref.get("id") or ""
        if not message_id or db.is_gmail_message_processed(message_id):
            continue
        try:
            full = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except Exception as exc:
            automation.log_event(
                automation.AUTOMATION_GMAIL_INBOX,
                automation.STATUS_ERROR,
                "Gmail fetch failed for {0}: {1}".format(message_id, exc),
            )
            messages.append("Gmail fetch failed for {0}: {1}".format(message_id, exc))
            continue
        messages.extend(process_message(full, message_id))

    gmail_config.touch_last_checked()
    if not messages:
        return ["Checked inbox — no new messages to process."]
    return messages
