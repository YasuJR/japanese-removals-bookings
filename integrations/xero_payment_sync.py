"""Sync booking payment status from Xero invoices (bank transfer / manual Xero payments)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import automation
import config
import database as db
import invoice
import invoice_numbering
from integrations import xero

logger = logging.getLogger(__name__)

SYNC_STATE_KEY = "xero_payment_sync"


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
            return None, "{0} mismatch".format(local_display)
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


def _booking_invoice_label(booking: Dict[str, Any]) -> str:
    return invoice_numbering.display_invoice_number(booking)


def load_sync_state() -> Dict[str, Any]:
    return dict(db.get_integration_settings(SYNC_STATE_KEY) or {})


def save_sync_state(data: Dict[str, Any]) -> None:
    db.save_integration_settings(SYNC_STATE_KEY, data)


def format_sync_timestamp(iso_value: str) -> str:
    """Display e.g. 17 Aug 2026 4:15 PM in app timezone."""
    text = (iso_value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    local = dt.astimezone(ZoneInfo(config.TIMEZONE))
    hour = local.strftime("%I").lstrip("0") or "12"
    return "{0} {1} {2} {3}:{4} {5}".format(
        local.day,
        local.strftime("%b"),
        local.year,
        hour,
        local.strftime("%M"),
        local.strftime("%p"),
    )


def dashboard_last_sync_display() -> str:
    state = load_sync_state()
    iso_value = (state.get("last_success_at") or "").strip()
    if not iso_value:
        return ""
    formatted = format_sync_timestamp(iso_value)
    return formatted


def sync_booking_payment_from_xero(booking: Dict[str, Any]) -> Dict[str, Any]:
    """
    One-way sync: Xero fully paid → booking payment_status Paid.
    Never downgrades a manually marked Paid booking to Unpaid.
    """
    booking_id = int(booking["id"])
    invoice_label = _booking_invoice_label(booking)
    result: Dict[str, Any] = {
        "booking_id": booking_id,
        "invoice_label": invoice_label,
        "ok": True,
        "updated": False,
        "skipped": False,
        "message": "",
        "log_lines": [],
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
        if "mismatch" in fetch_error.lower():
            result["log_lines"].append(fetch_error)
        else:
            result["log_lines"].append("{0}: {1}".format(invoice_label, fetch_error))
        return result
    if not inv:
        result["skipped"] = True
        result["message"] = "No matching Xero invoice."
        return result

    result["log_lines"].append("{0} matched".format(invoice_label))

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
    result["updated"] = True
    result["message"] = "{0} marked Paid from Xero.".format(invoice_label)
    result["log_lines"].append(
        "{0} changed UNPAID -> PAID".format(invoice_label)
    )
    automation.log_event(
        automation.AUTOMATION_XERO_PAYMENT_SYNC,
        automation.STATUS_SUCCESS,
        result["message"],
        booking_id=booking_id,
    )
    return result


def sync_xero_payments(*, source: str = "manual") -> Dict[str, Any]:
    """
    Pull paid Xero invoices and mark matching bookings as Paid.

    Used by Dashboard manual sync and Render cron (every 15 minutes).
    """
    started_at = datetime.now(ZoneInfo("UTC")).isoformat()
    log_lines: List[str] = ["Xero payment sync started"]
    summary: Dict[str, Any] = {
        "ok": False,
        "message": "",
        "updated": 0,
        "checked": 0,
        "skipped": 0,
        "errors": [],
        "log_lines": log_lines,
        "source": source,
    }

    if not xero.is_ready():
        message = "Connect Xero in Settings first."
        log_lines.append("Xero API authentication failed — connect Xero in Settings")
        log_lines.append("Xero payment sync completed")
        summary["message"] = message
        save_sync_state(
            {
                "last_run_at": started_at,
                "last_source": source,
                "last_ok": False,
                "last_message": message,
                "last_log_lines": log_lines,
            }
        )
        for line in log_lines:
            logger.info(line)
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
            invoice_label = _booking_invoice_label(booking)
            error_message = str(exc) or "Unexpected error."
            if "auth" in error_message.lower():
                error_message = "Xero API authentication failed"
            errors.append(
                {
                    "booking_id": booking.get("id"),
                    "invoice_label": invoice_label,
                    "message": error_message,
                }
            )
            log_lines.append("{0}: {1}".format(invoice_label, error_message))
            continue

        log_lines.extend(outcome.get("log_lines") or [])

        if not outcome.get("ok"):
            errors.append(
                {
                    "booking_id": outcome.get("booking_id"),
                    "invoice_label": outcome.get("invoice_label"),
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

    log_lines.append("{0} invoices checked".format(checked))
    log_lines.append("{0} booking(s) updated".format(updated))
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

    log_lines.append("Xero payment sync completed")
    finished_at = datetime.now(ZoneInfo("UTC")).isoformat()
    state = {
        "last_run_at": finished_at,
        "last_source": source,
        "last_ok": True,
        "last_message": summary["message"],
        "last_updated": updated,
        "last_checked": checked,
        "last_log_lines": log_lines,
    }
    if not errors:
        state["last_success_at"] = finished_at
    save_sync_state(state)

    for line in log_lines:
        logger.info(line)

    return summary
