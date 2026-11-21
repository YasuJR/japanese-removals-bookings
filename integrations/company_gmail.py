"""Company Gmail (info@japaneseremovals.com): sent "Confirmed" emails -> Check to Confirm bookings.

Read-only (gmail.readonly) Google login of the company account, kept apart from the
Google Calendar login (own scopes, own token in PostgreSQL integration_settings
"company_gmail" - nothing on disk, nothing in GitHub). Every 5 minutes a background
thread in the web service (one worker at a time, db.try_acquire_lease) lists SENT
messages containing "Confirmed" that were sent after the moment checking was turned on
(older emails are never imported). For each new message:

  - the word "Confirmed" must be in the text we wrote (not only in a quoted reply);
  - the whole thread (plus older threads with the same customer) is read by Claude
    (gmail_confirmed_parser) and classified booking / amendment / payment / cancellation;
  - booking  -> a new booking with status "Check to Confirm" (missing / uncertain fields
                highlighted), unless the customer already has a booking for that thread or
                an upcoming one: then it is linked to that booking for review instead;
  - amendment / cancellation -> linked to the matching booking for review (nothing changed);
  - payment / other -> recorded and skipped.

Gmail message ids are stored (gmail_confirmations, UNIQUE), so nothing is processed twice.
Nothing is sent: no email, SMS or push. Gmail is never modified (read-only scope).
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import automation
import callout_pricing
import config
import database as db
import job_status
from integrations import company_config, gmail_confirmed_parser as parser, google_oauth

logger = logging.getLogger(__name__)

STORAGE_KEY = "company_gmail"
LEASE_KEY = "company_gmail_poll_lease"  # every 5 min, one worker (not released)
RUNNING_KEY = "company_gmail_running"  # one check at a time (background or Check now)
RUNNING_SECONDS = 900
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
POLL_SECONDS = 300
TICK_SECONDS = 30
SOURCE = "gmail_confirmed"
MAX_MESSAGES_PER_RUN = 25
MAX_AI_ATTEMPTS = 3
COMPANY_ADDRESSES = [config.COMPANY_GMAIL_ADDRESS, "info@japaneseremovals.com.au"]

FIELD_LABELS = {
    "customer_name": "Customer name",
    "email": "Email",
    "phone": "Phone",
    "move_date": "Move date",
    "start_time": "Start time",
    "pickup_address": "Pickup address",
    "delivery_address": "Delivery address",
    "num_movers": "Movers",
    "hourly_rate": "Hourly rate",
    "callout_fee": "Call-out",
    "callout_minutes": "Call-out",
    "additional_charges": "Additional charges",
    "furniture": "Furniture",
    "notes": "Notes",
}


# --- settings (PostgreSQL integration_settings) ---------------------------------------


def _settings() -> Dict[str, Any]:
    return db.get_integration_settings(STORAGE_KEY)


def _update(**changes: Any) -> Dict[str, Any]:
    data = _settings()
    data.update(changes)
    db.save_integration_settings(STORAGE_KEY, data)
    return data


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def is_connected() -> bool:
    return bool((_settings().get("token_json") or "").strip())


def is_enabled() -> bool:
    data = _settings()
    return bool(data.get("enabled")) and bool((data.get("token_json") or "").strip())


def status_for_page() -> Dict[str, Any]:
    data = _settings()
    start_ms = int(data.get("start_after_ms") or 0)
    return {
        "connected": bool((data.get("token_json") or "").strip()),
        "account_email": data.get("account_email") or "",
        "expected_email": config.COMPANY_GMAIL_ADDRESS,
        "enabled": bool(data.get("enabled")),
        "connected_at": data.get("connected_at") or "",
        "start_after": datetime.utcfromtimestamp(start_ms / 1000).isoformat(timespec="seconds") if start_ms else "",
        "last_checked_at": data.get("last_checked_at") or "",
        "last_result": data.get("last_result") or "",
        "last_error": data.get("last_error") or "",
        "ai_ready": parser.ai_available(),
        "token_refreshed_at": data.get("token_refreshed_at") or "",
        "health_warning": health_warning(data),
        "polling_on": polling_wanted(),
        "poll_minutes": POLL_SECONDS // 60,
    }


def health_warning(data: Optional[Dict[str, Any]] = None) -> str:
    """Shown on the dashboard when checking is on but not working (login expired, no recent check)."""
    data = data if data is not None else _settings()
    if not data.get("enabled"):
        return ""
    if not (data.get("token_json") or "").strip():
        return "Company Gmail is not connected — reconnect it in Settings."
    if data.get("last_error"):
        return str(data.get("last_error"))
    last = data.get("last_checked_at") or ""
    try:
        age = datetime.utcnow() - datetime.fromisoformat(last)
    except ValueError:
        age = None
    if polling_wanted() and (age is None or age > timedelta(minutes=30)):
        return "Company Gmail has not been checked for over 30 minutes."
    return ""


def set_enabled(enabled: bool) -> None:
    """Turning checking on starts from now: emails sent before this moment are never imported."""
    if enabled:
        _update(enabled=True, start_after_ms=int(time.time() * 1000), last_error="")
    else:
        _update(enabled=False)


def disconnect() -> None:
    _update(token_json="", account_email="", enabled=False, connected_at="")


# --- Google login (read-only, company account only) ------------------------------------


def begin_connect(redirect_uri: str) -> Tuple[str, str]:
    flow = google_oauth.make_flow_for_scopes(redirect_uri, SCOPES)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        login_hint=config.COMPANY_GMAIL_ADDRESS,
    )
    return auth_url, state


def complete_connect(redirect_uri: str, authorization_response: str) -> Tuple[bool, str]:
    from googleapiclient.discovery import build

    flow = google_oauth.make_flow_for_scopes(redirect_uri, SCOPES)
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials
    granted = set(creds.scopes or [])
    if not set(SCOPES) <= granted:
        return False, "Gmail read permission was not granted. Please try again and allow it."
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    account = (service.users().getProfile(userId="me").execute().get("emailAddress") or "").lower()
    if account != config.COMPANY_GMAIL_ADDRESS:
        return False, "Signed in as {0}. Please sign in with {1}.".format(account or "another account",
                                                                           config.COMPANY_GMAIL_ADDRESS)
    _update(token_json=creds.to_json(), account_email=account, connected_at=_now_iso(), last_error="")
    return True, "Company Gmail connected ({0}, read-only).".format(account)


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    raw = (_settings().get("token_json") or "").strip()
    if not raw:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 — revoked / expired refresh token
            _update(last_error="Google login expired — reconnect the company Gmail ({0}).".format(
                type(exc).__name__))
            return None
        _update(token_json=creds.to_json(), token_refreshed_at=_now_iso())
        return creds
    return None


def gmail_service():
    from googleapiclient.discovery import build

    creds = _credentials()
    if creds is None:
        return None
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def thread_url(thread_id: str) -> str:
    if not thread_id:
        return ""
    return "https://mail.google.com/mail/?authuser={0}#all/{1}".format(
        quote(config.COMPANY_GMAIL_ADDRESS), quote(thread_id)
    )


# --- polling --------------------------------------------------------------------------


def _list_new_message_ids(service, start_after_ms: int) -> List[str]:
    query = "in:sent confirmed after:{0}".format(max(0, start_after_ms // 1000))
    ids: List[str] = []
    token = None
    while True:
        resp = service.users().messages().list(userId="me", q=query, maxResults=100, pageToken=token).execute()
        ids.extend(ref["id"] for ref in resp.get("messages") or [] if ref.get("id"))
        token = resp.get("nextPageToken")
        if not token or len(ids) >= 500:
            break
    return ids


def run_check(max_messages: int = MAX_MESSAGES_PER_RUN) -> Dict[str, Any]:
    """poll_once() guarded so the background check and "Check now" never run together."""
    if not db.try_acquire_lease(RUNNING_KEY, RUNNING_SECONDS):
        return {"ok": False, "message": "A check is already running — try again in a minute."}
    try:
        return poll_once(max_messages=max_messages)
    finally:
        db.release_lease(RUNNING_KEY)


def poll_once(
    service=None,
    ask: Optional[Callable[[str, str, str], Dict[str, Any]]] = None,
    today_iso: str = "",
    max_messages: int = MAX_MESSAGES_PER_RUN,
) -> Dict[str, Any]:
    """One check. Returns counts; used by the background thread, "Check now" and tests."""
    data = _settings()
    if not data.get("enabled"):
        return {"ok": False, "message": "Company Gmail checking is turned off."}
    start_after_ms = int(data.get("start_after_ms") or 0)
    if not start_after_ms:
        return {"ok": False, "message": "Turn checking on first."}
    service = service or gmail_service()
    if service is None:
        msg = data.get("last_error") or "Company Gmail is not connected."
        _update(last_checked_at=_now_iso(), last_result=msg)
        return {"ok": False, "message": msg}
    ask = ask or parser.ask_claude
    if not today_iso:
        from dashboard_data import perth_today

        today_iso = perth_today().isoformat()

    counts = {"created": 0, "linked": 0, "skipped": 0, "errors": 0}
    try:
        ids = _list_new_message_ids(service, start_after_ms)
    except Exception as exc:  # noqa: BLE001
        msg = "Gmail could not be read ({0}).".format(type(exc).__name__)
        _update(last_checked_at=_now_iso(), last_result=msg, last_error=msg)
        return {"ok": False, "message": msg}

    new_ids = [mid for mid in ids if not db.gmail_confirmation_exists(mid)]
    messages = []
    for mid in new_ids:
        try:
            msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception:  # noqa: BLE001
            counts["errors"] += 1
            continue
        if int(msg.get("internalDate") or 0) <= start_after_ms:
            continue  # sent before checking was turned on: never imported
        messages.append(msg)
    messages.sort(key=lambda m: int(m.get("internalDate") or 0))  # oldest first

    for msg in messages[:max_messages]:
        try:
            outcome = process_message(service, msg, ask, today_iso)
        except parser.ParseError as exc:
            outcome = _ai_failed(msg, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("company gmail: message failed")
            outcome = _ai_failed(msg, type(exc).__name__)
        counts[outcome] = counts.get(outcome, 0) + 1

    summary = "{created} created · {linked} linked for review · {skipped} skipped".format(**counts)
    if len(messages) > max_messages:
        summary += " · {0} more next time".format(len(messages) - max_messages)
    if counts["errors"]:
        summary += " · {0} will retry".format(counts["errors"])
    _update(last_checked_at=_now_iso(), last_result=summary, last_error="")
    if counts["created"] or counts["linked"]:
        automation.log_event(automation.AUTOMATION_GMAIL_INBOX, automation.STATUS_SUCCESS,
                             "Company Gmail: " + summary)
    return {"ok": True, "message": summary, **counts}


def preview_recent(limit: int = 3, service=None, ask=None, today_iso: str = "") -> List[Dict[str, Any]]:
    """Owner check (saves nothing): the latest sent "Confirmed" emails, what would be extracted
    and what would happen (new booking / link to an existing one / skip)."""
    service = service or gmail_service()
    if service is None:
        raise RuntimeError(_settings().get("last_error") or "Company Gmail is not connected.")
    ask = ask or parser.ask_claude
    if not today_iso:
        from dashboard_data import perth_today

        today_iso = perth_today().isoformat()
    resp = service.users().messages().list(userId="me", q="in:sent confirmed newer_than:30d", maxResults=30).execute()
    out: List[Dict[str, Any]] = []
    for ref in resp.get("messages") or []:
        msg = service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        if not parser.says_confirmed(msg):
            continue
        fields, context_used = _extract(service, msg, ask, today_iso)
        related = related_booking(msg.get("threadId") or "", fields["email"], fields["move_date"], today_iso)
        if db.gmail_confirmation_exists(msg["id"]):
            would = "already processed"
        elif fields["kind"] in ("payment", "other"):
            would = "skip ({0})".format(fields["kind"])
        elif related is not None:
            would = "link to booking #{0}".format(related["id"])
        elif fields["kind"] == "cancellation":
            would = "skip (cancellation, no booking in the app)"
        else:
            would = "new Check to Confirm booking"
        sent_ms = int(msg.get("internalDate") or 0)
        out.append({
            "message_id": msg["id"],
            "thread_url": thread_url(msg.get("threadId") or ""),
            "sent_at": datetime.utcfromtimestamp(sent_ms / 1000).isoformat(timespec="minutes") if sent_ms else "",
            "fields": fields,
            "would": would,
            "related_id": int(related["id"]) if related is not None else None,
            "older_threads_used": context_used,
            "before_start": sent_ms <= int(_settings().get("start_after_ms") or 0) or not _settings().get("enabled"),
        })
        if len(out) >= limit:
            break
    return out


def import_message(message_id: str, service=None, ask=None, today_iso: str = "") -> Dict[str, Any]:
    """Owner action: process one chosen sent email now, even if it was sent before checking was
    turned on. Same rules as the automatic check (dedupe, links, Check to Confirm)."""
    message_id = (message_id or "").strip()
    if not message_id:
        return {"ok": False, "message": "No email chosen."}
    if db.gmail_confirmation_exists(message_id):
        return {"ok": False, "message": "This email was already processed — nothing new was created."}
    service = service or gmail_service()
    if service is None:
        return {"ok": False, "message": _settings().get("last_error") or "Company Gmail is not connected."}
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    if "SENT" not in (msg.get("labelIds") or []):
        return {"ok": False, "message": "Only emails sent by the company can be imported."}
    if not today_iso:
        from dashboard_data import perth_today

        today_iso = perth_today().isoformat()
    outcome = process_message(service, msg, ask or parser.ask_claude, today_iso)
    rows = [r for r in db.list_gmail_confirmations(200) if r["message_id"] == message_id]
    row = rows[0] if rows else {}
    return {"ok": True, "outcome": outcome, "booking_id": row.get("booking_id"),
            "related_booking_id": row.get("related_booking_id"),
            "message": "Email processed: {0}.".format(outcome)}


def _extract(service, msg: Dict[str, Any], ask, today_iso: str) -> Tuple[Dict[str, Any], bool]:
    thread = _thread_messages(service, msg.get("threadId") or "")
    thread_text = parser.transcript(thread, COMPANY_ADDRESSES, upto_id=msg.get("id") or "")
    customer = parser.customer_address(msg, COMPANY_ADDRESSES)
    context = _older_threads_text(service, customer, msg.get("threadId") or "")
    fields = parser.clean(ask(thread_text, context, today_iso))
    if not fields["email"] and customer:
        fields["email"] = customer  # the address we sent the confirmation to
        fields["missing"] = [f for f in fields["missing"] if f != "email"]
    return fields, bool(context)


def _ai_failed(msg: Dict[str, Any], reason: str) -> str:
    """Retry a failed message on the next runs; after MAX_AI_ATTEMPTS record it as an error."""
    data = _settings()
    attempts = dict(data.get("attempts") or {})
    mid = msg.get("id") or ""
    attempts[mid] = int(attempts.get(mid) or 0) + 1
    if attempts[mid] < MAX_AI_ATTEMPTS:
        _update(attempts=attempts)
        return "errors"
    attempts.pop(mid, None)
    _update(attempts=attempts)
    _record(msg, kind="", outcome="error", summary="Could not read this email: {0}".format(reason)[:300])
    return "errors"


def _record(msg: Dict[str, Any], **fields: Any) -> int:
    h = parser.headers(msg)
    sent_ms = int(msg.get("internalDate") or 0)
    row = {
        "message_id": msg.get("id") or "",
        "thread_id": msg.get("threadId") or "",
        "sent_at": datetime.utcfromtimestamp(sent_ms / 1000).isoformat(timespec="seconds") if sent_ms else "",
        "subject": (h.get("subject") or "")[:300],
        "customer_email": parser.customer_address(msg, COMPANY_ADDRESSES),
    }
    row.update(fields)
    return db.add_gmail_confirmation(row)


def _thread_messages(service, thread_id: str) -> List[Dict[str, Any]]:
    thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    return thread.get("messages") or []


def _older_threads_text(service, customer: str, thread_id: str) -> str:
    """Up to 3 older threads mentioning the customer's address (quote request with the addresses)."""
    if not customer:
        return ""
    try:
        resp = service.users().threads().list(
            # quoted address = sender, recipient or in the body (website quote form relay)
            userId="me", q='"{0}" newer_than:180d'.format(customer.replace('"', "")), maxResults=6
        ).execute()
    except Exception:  # noqa: BLE001
        return ""
    texts = []
    for ref in resp.get("threads") or []:
        if ref.get("id") == thread_id:
            continue
        try:
            texts.append(parser.transcript(_thread_messages(service, ref["id"]), COMPANY_ADDRESSES))
        except Exception:  # noqa: BLE001
            continue
        if len(texts) >= 3:
            break
    return "\n\n=== older thread ===\n".join(texts)


def _active(booking: Dict[str, Any]) -> bool:
    return job_status.display(booking) != "Cancelled"


def related_booking(thread_id: str, email: str, move_date: str, today_iso: str) -> Optional[Dict[str, Any]]:
    """Existing booking this email is about: same Gmail thread, or same customer email with
    the same move date or an upcoming booking."""
    for booking in reversed(db.bookings_for_gmail_thread(thread_id)):
        if _active(booking):
            return booking
    if not email:
        return None
    same_customer = [b for b in db.bookings_for_customer_email(email) if _active(b)]
    if move_date:
        for booking in reversed(same_customer):
            if (booking.get("move_date") or "")[:10] == move_date:
                return booking
    upcoming = [b for b in same_customer if (b.get("move_date") or "")[:10] >= today_iso]
    return upcoming[-1] if upcoming else None


def process_message(service, msg: Dict[str, Any], ask, today_iso: str) -> str:
    """Handle one sent message. Returns created / linked / skipped (errors raise)."""
    if not parser.says_confirmed(msg):
        _record(msg, kind="", outcome="skipped", summary="'Confirmed' only in a quoted earlier email.")
        return "skipped"

    fields, _context_used = _extract(service, msg, ask, today_iso)
    kind = fields["kind"]
    summary = fields.get("summary") or fields.get("kind_reason") or kind
    details = json.dumps(fields, ensure_ascii=False)

    if kind in ("payment", "other"):
        _record(msg, kind=kind, outcome="skipped", summary=summary, details_json=details)
        return "skipped"

    related = related_booking(msg.get("threadId") or "", fields["email"], fields["move_date"], today_iso)
    if related is not None:
        review_kind = kind
        if kind == "booking":
            review_kind = "duplicate" if (related.get("move_date") or "")[:10] == fields["move_date"] else "amendment"
        _record(msg, kind=review_kind, outcome="linked", summary=summary,
                related_booking_id=int(related["id"]), details_json=details)
        return "linked"

    if kind == "cancellation":
        _record(msg, kind=kind, outcome="skipped", summary=summary + " (no booking in the app)",
                details_json=details)
        return "skipped"

    # Claim the message first (UNIQUE message_id), then create: a crash in between can never
    # lead to a second booking for the same email on the next run.
    record_id = _record(msg, kind=kind, outcome="created", summary=summary, details_json=details)
    booking_id = create_check_booking(msg, fields)
    db.set_gmail_confirmation_booking(record_id, booking_id)
    return "created"


def _notes_text(fields: Dict[str, Any], kind: str) -> str:
    parts = []
    if fields.get("notes"):
        parts.append(fields["notes"])
    if fields.get("furniture"):
        parts.append("Furniture: " + fields["furniture"])
    if kind == "amendment":
        parts.append("Email confirmed a change to an earlier booking that is not in the app — please check.")
    return "\n\n".join(parts)


def create_check_booking(msg: Dict[str, Any], fields: Dict[str, Any]) -> int:
    """New booking, status Check to Confirm. Values are only what the email says (no defaults
    for customer details); the review list says what to fill in."""
    defaults = company_config.booking_form_defaults()
    rate = fields.get("hourly_rate")
    minutes = fields.get("callout_minutes")
    fee = fields.get("callout_fee")
    if minutes is None and fee and rate:
        minutes = callout_pricing.infer_minutes_from_legacy(rate, fee)
    if minutes is not None and rate:
        fee = callout_pricing.fee_amount(rate, minutes)
    booking_id = db.create_booking(
        customer_name=fields.get("customer_name") or "",
        phone=fields.get("phone") or "",
        email=fields.get("email") or "",
        pickup_address=fields.get("pickup_address") or "",
        delivery_address=fields.get("delivery_address") or "",
        move_date=fields.get("move_date") or "",
        num_movers=int(fields.get("num_movers") or 0),
        notes=_notes_text(fields, fields.get("kind") or ""),
        start_time=fields.get("start_time") or "",
        hourly_rate=float(rate or 0),
        callout_fee=float(fee or 0),
        callout_minutes=minutes,
        gst_enabled=1 if defaults.get("gst_enabled") else 0,
        status=job_status.CHECK_TO_CONFIRM,
        gmail_message_id=msg.get("id") or "",
    )
    db.update_booking_integration_fields(booking_id, {"source": SOURCE})
    charges = [
        {"description": c["description"], "quantity": 1, "unit_price": c["amount"]}
        for c in fields.get("additional_charges") or []
    ]
    if charges:
        db.replace_extra_charges(booking_id, charges)
    review = {
        "message_id": msg.get("id") or "",
        "thread_id": msg.get("threadId") or "",
        "kind": fields.get("kind"),
        "summary": fields.get("summary") or "",
        "missing": fields.get("missing") or [],
        "uncertain": fields.get("uncertain") or [],
        "imported_at": _now_iso(),
    }
    db.save_booking_gmail_fields(booking_id, msg.get("threadId") or "", json.dumps(review, ensure_ascii=False))
    return booking_id


# --- review data for pages --------------------------------------------------------------


def review_for_booking(booking: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Highlights for a Check to Confirm booking (None for any other booking)."""
    if not job_status.is_check_to_confirm(booking):
        return None
    try:
        review = json.loads(booking.get("gmail_review_json") or "{}")
    except ValueError:
        review = {}
    def still_empty(field: str) -> bool:
        if field == "additional_charges":
            return True
        if field == "furniture":
            return "furniture" not in str(booking.get("notes") or "").lower()
        if field in ("callout_fee", "callout_minutes"):
            return booking.get("callout_minutes") in (None, "") and not float(booking.get("callout_fee") or 0)
        value = booking.get(field)
        if field in ("num_movers", "hourly_rate"):
            return not float(value or 0)
        return not str(value or "").strip()

    # Missing = only while still empty (fields the owner has since filled stop being yellow).
    missing = [f for f in review.get("missing") or [] if f in FIELD_LABELS and still_empty(f)]
    uncertain = [u for u in review.get("uncertain") or [] if isinstance(u, dict) and u.get("field") in FIELD_LABELS]
    highlight = sorted(set(missing) | {u["field"] for u in uncertain})
    form_fields = set(highlight)
    if "callout_fee" in form_fields or "callout_minutes" in form_fields:
        form_fields |= {"callout_fee", "callout_minutes"}
    if "furniture" in form_fields:
        form_fields.add("notes")
    return {
        "summary": review.get("summary") or "",
        "kind": review.get("kind") or "",
        "missing": [{"field": f, "label": FIELD_LABELS[f]} for f in missing],
        "uncertain": [{"field": u["field"], "label": FIELD_LABELS[u["field"]], "reason": u.get("reason") or ""}
                      for u in uncertain],
        "highlight_fields": sorted(form_fields),
        "gmail_url": thread_url(review.get("thread_id") or booking.get("gmail_thread_id") or ""),
    }


def dashboard_items() -> Dict[str, Any]:
    pending = []
    for booking in db.list_check_to_confirm_bookings():
        review = review_for_booking(booking) or {}
        pending.append({
            "booking": booking,
            "summary": review.get("summary") or "",
            "missing_count": len(review.get("missing") or []),
            "uncertain_count": len(review.get("uncertain") or []),
            "gmail_url": review.get("gmail_url") or "",
        })
    reviews = []
    for row in db.list_open_gmail_reviews():
        booking = db.get_booking(int(row["related_booking_id"]))
        reviews.append({
            "row": row,
            "booking": dict(booking) if booking else None,
            "gmail_url": thread_url(row.get("thread_id") or ""),
        })
    return {"pending": pending, "reviews": reviews, "count": len(pending), "warning": health_warning()}


# --- background thread ------------------------------------------------------------------

_thread_started = False
_thread_lock = threading.Lock()


def polling_wanted() -> bool:
    if config.COMPANY_GMAIL_POLLING is not None:
        return bool(config.COMPANY_GMAIL_POLLING)
    return bool(config.PRODUCTION)


def _loop() -> None:
    time.sleep(random.uniform(5, 20))
    while True:
        try:
            if is_enabled() and db.try_acquire_lease(LEASE_KEY, POLL_SECONDS - 15):
                run_check()
        except Exception:  # noqa: BLE001 — never let the thread die
            logger.exception("company gmail: background check failed")
        time.sleep(TICK_SECONDS)


def start_background_polling() -> bool:
    """Start the 5-minute checker once per worker process (wsgi.py). No-op when not wanted."""
    global _thread_started
    if not polling_wanted():
        return False
    with _thread_lock:
        if _thread_started:
            return False
        threading.Thread(target=_loop, name="company-gmail-poll", daemon=True).start()
        _thread_started = True
    return True
