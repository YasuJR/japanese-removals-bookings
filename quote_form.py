"""Phase 20 — public website quote form validation."""

import re
from datetime import date, datetime
from typing import Any, Dict, List, Tuple

import database as db

HONEYPOT_FIELD = "company_website"
RATE_LIMIT_MINUTES = 15
RATE_LIMIT_MAX = 3
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _suburb_address(suburb: str) -> str:
    text = (suburb or "").strip()
    if not text:
        return ""
    if "wa" in text.lower():
        return text
    return "{0}, WA".format(text)


def _bool_label(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in ("yes", "true", "1", "on"):
        return "Yes"
    if raw in ("no", "false", "0", "off"):
        return "No"
    return (value or "").strip() or "—"


def is_honeypot_triggered(form: Any) -> bool:
    return bool((form.get(HONEYPOT_FIELD) or "").strip())


def is_rate_limited(ip_address: str) -> bool:
    return db.quote_submission_count_recent(ip_address, RATE_LIMIT_MINUTES) >= RATE_LIMIT_MAX


def parse_quote_form(form: Any, ip_address: str = "") -> Tuple[Dict[str, Any], List[str], bool]:
    """
    Parse and validate quote form.
    Returns (data, errors, spam_blocked).
    spam_blocked=True when honeypot or rate limit — caller should show generic success.
    """
    if is_honeypot_triggered(form):
        return {}, [], True
    if ip_address and is_rate_limited(ip_address):
        return {}, ["Too many quote requests. Please try again later."], True

    name = (form.get("name") or "").strip()
    phone = (form.get("phone") or "").strip()
    email = (form.get("email") or "").strip()
    move_date = (form.get("move_date") or "").strip()
    pickup_suburb = (form.get("pickup_suburb") or "").strip()
    delivery_suburb = (form.get("delivery_suburb") or "").strip()
    bedrooms = (form.get("bedrooms") or "").strip()
    stairs = (form.get("stairs") or "").strip()
    piano = (form.get("piano") or "").strip()
    pool_table = (form.get("pool_table") or "").strip()
    packing = (form.get("packing_required") or "").strip()
    notes = (form.get("notes") or "").strip()

    errors: List[str] = []
    if not name:
        errors.append("Name is required.")
    if not phone:
        errors.append("Phone number is required.")
    if not email:
        errors.append("Email is required.")
    elif not EMAIL_RE.match(email):
        errors.append("Enter a valid email address.")
    if not move_date:
        errors.append("Move date is required.")
    else:
        try:
            parsed = datetime.strptime(move_date, "%Y-%m-%d").date()
            if parsed < date.today():
                errors.append("Move date cannot be in the past.")
        except ValueError:
            errors.append("Enter a valid move date.")
    if not pickup_suburb:
        errors.append("Pickup suburb is required.")
    if not delivery_suburb:
        errors.append("Delivery suburb is required.")

    num_movers = 2
    if bedrooms:
        try:
            beds = int(bedrooms)
            if beds < 1 or beds > 20:
                raise ValueError
            num_movers = min(max(beds, 1), 6)
        except ValueError:
            errors.append("Bedrooms must be a number between 1 and 20.")

    detail_lines = [
        "Website quote request",
        "Bedrooms: {0}".format(bedrooms or "—"),
        "Stairs: {0}".format(_bool_label(stairs)),
        "Piano: {0}".format(_bool_label(piano)),
        "Pool table: {0}".format(_bool_label(pool_table)),
        "Packing required: {0}".format(_bool_label(packing)),
    ]
    if notes:
        detail_lines.append("Notes: {0}".format(notes))
    combined_notes = "\n".join(detail_lines)

    data = {
        "customer_name": name,
        "phone": phone,
        "email": email,
        "move_date": move_date,
        "pickup_address": _suburb_address(pickup_suburb),
        "delivery_address": _suburb_address(delivery_suburb),
        "num_movers": num_movers,
        "notes": combined_notes,
        "pickup_suburb": pickup_suburb,
        "delivery_suburb": delivery_suburb,
    }
    return data, errors, False
