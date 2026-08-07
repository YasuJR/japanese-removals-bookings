"""Sequential local invoice numbers (1, 2, 3…) stored in the database."""

from datetime import date
from typing import Any, Dict

import database as db

DEFAULT_ABN = "93 645 845 227"


def display_invoice_number(booking: Dict[str, Any]) -> str:
    """Formatted invoice number for PDF/UI — empty when not yet assigned."""
    number = (booking.get("invoice_number") or "").strip()
    return number if number else "—"


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
