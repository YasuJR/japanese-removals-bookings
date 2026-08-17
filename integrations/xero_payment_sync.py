"""Sync booking payment status from Xero invoices (bank transfer / manual Xero payments)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import automation
import database as db
import invoice
import invoice_numbering
from integrations import xero


def invoice_reference_number(booking: Dict[str, Any]) -> Optional[int]:
    """Numeric invoice reference used to match Xero InvoiceNumber (e.g. 22 for INV22)."""
    return invoice_numbering.reference_number_for_booking(booking)


def xero_invoice_reference_number(inv: Dict[str, Any]) -> Optional[int]:
    return invoice_numbering.numeric_sequence_value(inv.get("InvoiceNumber"))


def invoice_numbers_match(booking: Dict[str, Any], inv: Dict[str, Any]) -> bool:
    """Match bookings to Xero invoices by invoice number — never by customer name."""
    booking_ref = invoice_reference_number(booking)
    xero_ref = xero_invoice_reference_number(inv)
    if booking_ref is None or xero_ref is None:
        return False
    return booking_ref == xero_ref


def is_xero_invoice_fully_paid(inv: Dict[str, Any]) -> bool:
    """True only when the Xero invoice is fully paid (not part-paid)."""
    amount_due = float(inv.get("AmountDue") or 0)
    amount_paid = float(inv.get("AmountPaid") or 0)
    total = float(inv.get("Total") or 0)

    if amount_due > 0.01:
        return False
    if amount_paid <= 0.01:
        return False
    if total > 0.01 and amount_paid + 0.01 < total:
        return False
    return True


def _invoice_lookup_candidates(booking: Dict[str, Any]) -> List[str]:
    seen = set()
    candidates: List[str] = []

    def add(value: str) -> None:
        text = (value or "").strip()
        if text and text not in seen:
            seen.add(text)
            candidates.append(text)

    stored = (booking.get("invoice_number") or "").strip()
    if stored:
        add(stored)
    ref = invoice_reference_number(booking)
    if ref is not None:
        add(str(ref))
        add("INV{0}".format(ref))
        add("INV-{0}".format(ref))
    return candidates


def fetch_xero_invoice_for_booking(
    booking: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Load the Xero invoice for a booking, matched by linked ID and/or invoice number.
    Returns (invoice, error_message).
    """
    invoice_id = (booking.get("xero_invoice_id") or "").strip()
    if xero.is_real_invoice_id(invoice_id):
        inv = xero.fetch_invoice(invoice_id)
        if not inv:
            return None, "Could not load linked Xero invoice."
        if not invoice_numbers_match(booking, inv):
            local_display = invoice_numbering.display_invoice_number(booking)
            xero_number = (inv.get("InvoiceNumber") or "").strip()
            return None, "Invoice number mismatch ({0} vs {1}).".format(
                local_display,
                xero_number or "—",
            )
        return inv, None

    for candidate in _invoice_lookup_candidates(booking):
        inv = xero.fetch_invoice_by_number(candidate)
        if inv and invoice_numbers_match(booking, inv):
            return inv, None
    return None, None


def _booking_eligible_for_sync(booking: Dict[str, Any]) -> bool:
    payment = invoice.normalize_payment_status(booking.get("payment_status"))
    if payment == invoice.PAYMENT_STATUS_PAID:
        return False
    if xero.is_real_invoice_id(booking.get("xero_invoice_id")):
        return True
    if (booking.get("invoice_number") or "").strip():
        return True
    return invoice_reference_number(booking) is not None


def sync_booking_payment_from_xero(booking: Dict[str, Any]) -> Dict[str, Any]:
    """
    One-way sync: Xero fully paid → booking payment_status Paid.
    Never downgrades a manually marked Paid booking to Unpaid.
    """
    booking_id = int(booking["id"])
    result: Dict[str, Any] = {
        "booking_id": booking_id,
        "ok": True,
        "updated": False,
        "skipped": False,
        "message": "",
    }

    if not xero.is_ready():
        result["ok"] = False
        result["message"] = "Connect Xero in Settings first."
        return result

    current = invoice.normalize_payment_status(booking.get("payment_status"))
    if current == invoice.PAYMENT_STATUS_PAID:
        result["skipped"] = True
        result["message"] = "Already Paid."
        return result

    inv, fetch_error = fetch_xero_invoice_for_booking(booking)
    if fetch_error:
        result["ok"] = False
        result["message"] = fetch_error
        return result
    if not inv:
        result["skipped"] = True
        result["message"] = "No matching Xero invoice."
        return result

    if not is_xero_invoice_fully_paid(inv):
        result["skipped"] = True
        result["message"] = "Not fully paid in Xero."
        return result

    payment_status, paid_at = xero.derive_payment_status_from_invoice(inv, booking)
    if payment_status != invoice.PAYMENT_STATUS_PAID:
        result["skipped"] = True
        result["message"] = "Not fully paid in Xero."
        return result

    xero.persist_invoice_from_xero(booking_id, inv)
    invoice.apply_payment_status(
        booking_id,
        invoice.PAYMENT_STATUS_PAID,
        paid_at=paid_at,
    )
    display_number = invoice_numbering.display_invoice_number(booking)
    result["updated"] = True
    result["message"] = "{0} marked Paid from Xero.".format(display_number)
    automation.log_event(
        automation.AUTOMATION_XERO_PAYMENT_SYNC,
        automation.STATUS_SUCCESS,
        result["message"],
        booking_id=booking_id,
    )
    return result


def sync_xero_payments() -> Dict[str, Any]:
    """
    Pull paid Xero invoices and mark matching bookings as Paid.

    Intended for Dashboard manual sync and future cron (Render every 5–15 minutes).
    """
    summary: Dict[str, Any] = {
        "ok": False,
        "message": "",
        "updated": 0,
        "checked": 0,
        "skipped": 0,
        "errors": [],
    }

    if not xero.is_ready():
        summary["message"] = "Connect Xero in Settings first."
        return summary

    updated = 0
    skipped = 0
    checked = 0
    errors: List[Dict[str, Any]] = []

    for row in db.list_all():
        booking = dict(row)
        if not _booking_eligible_for_sync(booking):
            continue
        checked += 1
        try:
            outcome = sync_booking_payment_from_xero(booking)
        except Exception as exc:
            errors.append(
                {
                    "booking_id": booking.get("id"),
                    "message": str(exc) or "Unexpected error.",
                }
            )
            continue

        if not outcome.get("ok"):
            errors.append(
                {
                    "booking_id": outcome.get("booking_id"),
                    "message": outcome.get("message") or "Sync failed.",
                }
            )
        elif outcome.get("updated"):
            updated += 1
        elif outcome.get("skipped"):
            skipped += 1

    summary["ok"] = True
    summary["updated"] = updated
    summary["checked"] = checked
    summary["skipped"] = skipped
    summary["errors"] = errors

    if errors:
        summary["message"] = "Synced {0} payment(s); {1} error(s).".format(
            updated,
            len(errors),
        )
        automation.log_event(
            automation.AUTOMATION_XERO_PAYMENT_SYNC,
            automation.STATUS_PARTIAL,
            summary["message"],
        )
    elif updated:
        summary["message"] = "Synced {0} payment(s) from Xero.".format(updated)
        automation.log_event(
            automation.AUTOMATION_XERO_PAYMENT_SYNC,
            automation.STATUS_SUCCESS,
            summary["message"],
        )
    else:
        summary["message"] = "No new paid invoices to sync from Xero."
        automation.log_event(
            automation.AUTOMATION_XERO_PAYMENT_SYNC,
            automation.STATUS_SUCCESS,
            summary["message"],
        )

    return summary
