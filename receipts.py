"""Payment receipts (separate from the tax invoice).

A receipt can be issued only for a booking whose Payment Status is Paid. It never
replaces the invoice: both stay available for the same booking.

Stored on bookings:
  receipt_number     "RCT138" — the same number as the invoice (INV138), so the two
                     papers pair up; fixed the first time the receipt is issued
  receipt_issued_at  UTC ISO time the receipt was first issued
  payment_method     Bank Transfer / Card / Cash / Other
  amount_paid        invoice total at issue time. Full payment only for now; with
                     Part Paid support this becomes the running amount and
                     balance = invoice total − amount_paid.
  paid_at            (existing) when the money was received

Amounts are never recalculated here: the receipt shows the invoice's own totals
(invoice.calculate_invoice_totals), so Subtotal / GST / Total match the invoice.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

import database as db
import invoice
import invoice_numbering

PAYMENT_METHODS = ("Bank Transfer", "Card", "Cash", "Other")
RECEIPT_PREFIX = "RCT"


def _row(booking: Any) -> Dict[str, Any]:
    return booking if isinstance(booking, dict) else dict(booking or {})


def is_paid(booking: Dict[str, Any]) -> bool:
    return invoice.normalize_payment_status(booking.get("payment_status")) == invoice.PAYMENT_STATUS_PAID


def normalize_method(value: Any) -> str:
    text = str(value or "").strip().lower()
    for method in PAYMENT_METHODS:
        if text == method.lower():
            return method
    if text in ("bank", "bank transfer", "eft", "transfer"):
        return "Bank Transfer"
    if text in ("credit card", "stripe", "card"):
        return "Card"
    return ""


def inferred_method(booking: Dict[str, Any]) -> str:
    """Stored method, else what the payment record shows (Stripe = Card), else ''."""
    stored = normalize_method(booking.get("payment_method"))
    if stored:
        return stored
    if str(booking.get("stripe_payment_status") or "").strip().lower() == "paid" or str(
        booking.get("stripe_payment_intent_id") or ""
    ).strip():
        return "Card"
    return ""


def payment_date(booking: Dict[str, Any]) -> Optional[date]:
    """Date the money was received (Perth), from paid_at. None when it was never recorded."""
    raw = booking.get("paid_at")
    if raw is None or str(raw).strip() == "":
        return None
    from sales_dashboard import paid_on_perth

    return paid_on_perth(booking)


def receipt_number_for(booking: Dict[str, Any]) -> str:
    """RCT + the invoice's number (INV138 → RCT138). Never collides with another booking's receipt."""
    number = invoice_numbering.reference_number_for_booking(booking)
    base = "{0}{1}".format(RECEIPT_PREFIX, number if number is not None else booking.get("id"))
    with db.get_connection() as conn:
        taken = conn.execute(
            "SELECT id FROM bookings WHERE receipt_number = ? AND id <> ?",
            (base, int(booking["id"])),
        ).fetchone()
    if taken:
        return "{0}-{1}".format(base, booking["id"])
    return base


def state(booking: Any) -> Dict[str, Any]:
    """What the office receipt panel needs (no writes)."""
    row = _row(booking)
    paid = is_paid(row)
    issued = bool(str(row.get("receipt_number") or "").strip()) and paid
    totals = invoice.calculate_invoice_totals(invoice.resolve_booking_invoice(row)) if paid else None
    amount_paid = row.get("amount_paid")
    changed = bool(
        issued
        and totals is not None
        and amount_paid not in (None, "")
        and abs(float(amount_paid) - float(totals["total"])) > 0.005
    )
    paid_on = payment_date(row)
    return {
        "paid": paid,
        "issued": issued,
        "receipt_number": str(row.get("receipt_number") or "").strip(),
        "payment_method": inferred_method(row),
        "payment_date": paid_on.isoformat() if paid_on else "",
        "payment_date_recorded": paid_on is not None,
        "total": totals["total"] if totals else None,
        "amount_paid": float(amount_paid) if amount_paid not in (None, "") else None,
        "invoice_changed": changed,
        "methods": PAYMENT_METHODS,
    }


def issue(booking_id: int, *, method: Any, paid_on: Any) -> Tuple[bool, str]:
    """Issue (or update) the receipt for a Paid booking. Keeps the receipt number once given."""
    row = db.get_booking(booking_id)
    if not row:
        return False, "Booking not found."
    booking = dict(row)
    if not is_paid(booking):
        return False, "Receipt available after payment (Payment Status must be Paid)."
    method_value = normalize_method(method)
    if not method_value:
        return False, "Choose the payment method."
    try:
        paid_date = datetime.strptime(str(paid_on or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return False, "Enter the payment date."
    from dashboard_data import perth_today

    if paid_date > perth_today():
        return False, "Payment date cannot be in the future."

    totals = invoice.calculate_invoice_totals(invoice.resolve_booking_invoice(booking))
    fields: Dict[str, Any] = {
        "payment_method": method_value,
        "amount_paid": totals["total"],
    }
    recorded = payment_date(booking)
    if recorded != paid_date:
        # A date-only paid_at is read as that Perth calendar day (sales_dashboard.paid_on_perth).
        fields["paid_at"] = paid_date.isoformat()
    number = str(booking.get("receipt_number") or "").strip()
    if not number:
        fields["receipt_number"] = receipt_number_for(booking)
        fields["receipt_issued_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.update_booking_receipt_fields(booking_id, fields)
    final_number = fields.get("receipt_number") or number
    if number:
        return True, "Receipt {0} updated.".format(final_number)
    return True, "Receipt {0} issued.".format(final_number)
