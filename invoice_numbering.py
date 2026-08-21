"""Sequential local invoice numbers (1, 2, 3…) stored in the database."""

import re
from datetime import date
from typing import Any, Dict, Optional

import database as db

DEFAULT_ABN = "93 645 845 227"
_INVOICE_NUMERIC_RE = re.compile(r"^INV-?(\d+)$", re.I)


def _numeric_suffix(raw: Optional[str]) -> Optional[int]:
    """Parse a plain or INV-prefixed invoice reference into its numeric part."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = _INVOICE_NUMERIC_RE.match(text)
    if match:
        return int(match.group(1))
    return None


def reference_number_for_booking(booking: Dict[str, Any]) -> Optional[int]:
    """
    Numeric invoice reference for display — stored invoice_number first, else booking id.
    Does not modify stored data.
    """
    if not isinstance(booking, dict):
        booking = dict(booking)
    stored = _numeric_suffix((booking.get("invoice_number") or "").strip())
    if stored is not None:
        return stored
    booking_id = booking.get("id")
    if booking_id is not None and str(booking_id).strip().isdigit():
        return int(booking_id)
    return None


def format_invoice_number(raw: Optional[str]) -> str:
    """
    Display format for a stored invoice number — INV25, INV100 (no zero-padding).

    Plain numeric values (e.g. "25") become INV25. INV-prefixed values are
    normalised. Non-standard stored values (e.g. legacy Xero codes) are returned
    unchanged.
    """
    num = _numeric_suffix(raw)
    if num is not None:
        return "INV{0}".format(num)
    text = (raw or "").strip()
    return text


def numeric_sequence_value(raw: Optional[str]) -> int:
    """Extract the numeric sequence from a stored invoice number, if any."""
    num = _numeric_suffix(raw)
    return num if num is not None else 0


def display_invoice_number(booking: Dict[str, Any]) -> str:
    """Formatted invoice number for PDF/UI — uses booking id when none stored."""
    if not isinstance(booking, dict):
        booking = dict(booking)
    ref = reference_number_for_booking(booking)
    if ref is not None:
        return "INV{0}".format(ref)
    legacy = (booking.get("invoice_number") or "").strip()
    if legacy:
        return legacy
    return "—"


def stored_invoice_number_display(booking: Any) -> str:
    """
    Official invoice number for Dashboard when one is already issued.

    Returns '' when the booking has no stored invoice_number. Does not
    allocate a new number or fall back to the booking id.
    """
    if booking is None:
        return ""
    if not isinstance(booking, dict):
        if hasattr(booking, "keys"):
            booking = dict(booking)
        else:
            return ""
    stored = str(booking.get("invoice_number") or "").strip()
    if not stored:
        return ""
    return format_invoice_number(stored)


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
