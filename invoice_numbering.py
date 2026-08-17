"""Sequential local invoice numbers (1, 2, 3…) stored in the database."""

import re
from datetime import date
from typing import Any, Dict, Optional

import database as db

DEFAULT_ABN = "93 645 845 227"
_INVOICE_NUMERIC_RE = re.compile(r"^INV-(\d+)$", re.I)


def format_invoice_number(raw: Optional[str]) -> str:
    """
    Display format for a stored invoice number — INV-XXXX (4-digit suffix).

    Plain numeric values (e.g. "25") become INV-0025. Already-formatted INV-XXXX
    values are normalised. Non-standard stored values (e.g. legacy Xero codes) are
    returned unchanged.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return "INV-{0:04d}".format(int(text))
    match = _INVOICE_NUMERIC_RE.match(text)
    if match:
        return "INV-{0:04d}".format(int(match.group(1)))
    return text


def numeric_sequence_value(raw: Optional[str]) -> int:
    """Extract the numeric sequence from a stored invoice number, if any."""
    text = (raw or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    match = _INVOICE_NUMERIC_RE.match(text)
    if match:
        return int(match.group(1))
    return 0


def display_invoice_number(booking: Dict[str, Any]) -> str:
    """Formatted invoice number for PDF/UI — em dash when not yet assigned."""
    raw = (booking.get("invoice_number") or "").strip()
    if not raw:
        return "—"
    formatted = format_invoice_number(raw)
    return formatted if formatted else "—"


def ensure_booking_invoice_number(booking_id: int) -> str:
    """
    Assign the next sequential invoice number when a booking has none yet.
    Editing, PDF download, and preview must not call this.
    """
    row = db.get_booking(booking_id)
    if not row:
        return ""
    booking = dict(row)
    existing = (booking.get("invoice_number") or "").strip()
    if existing:
        return existing

    number = str(db.allocate_invoice_number())
    fields = {"invoice_number": number}
    if not (booking.get("invoice_issue_date") or "").strip():
        fields["invoice_issue_date"] = date.today().isoformat()
    db.update_booking_invoice_fields(booking_id, fields)
    return number
