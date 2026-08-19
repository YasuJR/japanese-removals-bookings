"""Sync booking payment status from Xero invoices (bank transfer / manual Xero payments)."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import automation
import config
import database as db
import invoice
import invoice_numbering
from integrations import xero

logger = logging.getLogger(__name__)

SYNC_STATE_KEY = "xero_payment_sync"

# Xero Accounting API invoice statuses treated as fully paid when amounts agree.
# AUTHORISED is included only when AmountDue is ~0 and AmountPaid covers Total
# (Xero can lag on flipping the status to PAID after a bank-transfer payment).
XERO_FULLY_PAID_STATUSES = frozenset({"PAID"})
XERO_AMOUNT_PAID_STATUSES = frozenset({"PAID", "AUTHORISED"})
XERO_NEVER_PAID_STATUSES = frozenset({"DRAFT", "DELETED", "VOIDED", "SUBMITTED"})


def _sync_timestamp() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).isoformat()


def stored_invoice_reference_number(booking: Dict[str, Any]) -> Optional[int]:
    """
    Numeric invoice reference from the stored invoice_number only.

    Does not fall back to booking id — matching INV31 to booking 31 would be unsafe
    when that booking's Xero invoice is a different number.
    """
    stored = ""
    if hasattr(booking, "keys") and not isinstance(booking, dict):
        try:
            stored = str(booking["invoice_number"] or "").strip()
        except (KeyError, TypeError):
            stored = ""
    else:
        stored = str((booking or {}).get("invoice_number") or "").strip()
    if not stored:
        return None
    value = invoice_numbering.numeric_sequence_value(stored)
    return value if value else None


def invoice_reference_number(booking: Dict[str, Any]) -> Optional[int]:
    """Numeric invoice reference used to match Xero InvoiceNumber (e.g. 31 for INV31)."""
    return stored_invoice_reference_number(booking)


def xero_invoice_reference_number(inv: Dict[str, Any]) -> Optional[int]:
    value = invoice_numbering.numeric_sequence_value(inv.get("InvoiceNumber"))
    return value if value else None


def format_invoice_label(booking: Dict[str, Any], inv: Optional[Dict[str, Any]] = None) -> str:
    if inv:
        xero_number = str(inv.get("InvoiceNumber") or "").strip()
        if xero_number:
            formatted = invoice_numbering.format_invoice_number(xero_number)
            return formatted or xero_number
    stored = str((booking or {}).get("invoice_number") or "").strip()
    if stored:
        return invoice_numbering.format_invoice_number(stored) or stored
    ref = stored_invoice_reference_number(booking)
    if ref is not None:
        return "INV{0}".format(ref)
    return "—"


def invoice_numbers_match(booking: Dict[str, Any], inv: Dict[str, Any]) -> bool:
    """Match bookings to Xero invoices by invoice number — never by customer name."""
    booking_ref = stored_invoice_reference_number(booking)
    xero_ref = xero_invoice_reference_number(inv)
    if booking_ref is not None and xero_ref is not None:
        return booking_ref == xero_ref
    # Linked Xero invoice ID is allowed when the booking has no stored number yet.
    if booking_ref is None and xero.is_real_invoice_id(booking.get("xero_invoice_id")):
        return xero_ref is not None or bool(str(inv.get("InvoiceID") or "").strip())
    return False


def is_xero_invoice_fully_paid(inv: Dict[str, Any]) -> bool:
    """True only when the Xero invoice is fully paid (not part-paid, voided, or draft)."""
    status = str(inv.get("Status") or "").strip().upper()
    if status in XERO_NEVER_PAID_STATUSES:
        return False
    if status not in XERO_AMOUNT_PAID_STATUSES:
        return False

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

    stored = str((booking.get("invoice_number") or "")).strip()
    if stored:
        add(stored)
    ref = stored_invoice_reference_number(booking)
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
    invoice_id = str(booking.get("xero_invoice_id") or "").strip()
    stored_ref = stored_invoice_reference_number(booking)
    if xero.is_real_invoice_id(invoice_id):
        inv = xero.fetch_invoice(invoice_id)
        if not inv:
            return None, "Could not load linked Xero invoice."
        xero_ref = xero_invoice_reference_number(inv)
        if stored_ref is not None and xero_ref is not None and stored_ref != xero_ref:
            local_display = format_invoice_label(booking)
            xero_number = str(inv.get("InvoiceNumber") or "").strip() or "—"
            return None, "unmatched invoice={0} booking_id={1} reason=Xero invoice number mismatch (Xero={2})".format(
                local_display,
                booking.get("id"),
                xero_number,
            )
        return inv, None

    if stored_ref is None:
        return None, None

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
    return stored_invoice_reference_number(booking) is not None


def _booking_invoice_label(booking: Dict[str, Any]) -> str:
    return format_invoice_label(booking)


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


def _index_bookings_by_invoice_keys(
    bookings: Iterable[Dict[str, Any]],
) -> Tuple[Dict[int, List[int]], Dict[str, List[int]]]:
    by_ref: Dict[int, List[int]] = defaultdict(list)
    by_xero_id: Dict[str, List[int]] = defaultdict(list)
    for booking in bookings:
        booking_id = int(booking["id"])
        ref = stored_invoice_reference_number(booking)
        if ref is not None and booking_id not in by_ref[ref]:
            by_ref[ref].append(booking_id)
        xid = str(booking.get("xero_invoice_id") or "").strip()
        if xero.is_real_invoice_id(xid) and booking_id not in by_xero_id[xid]:
            by_xero_id[xid].append(booking_id)
    return by_ref, by_xero_id


def _ambiguous_match_message(
    booking: Dict[str, Any],
    by_ref: Dict[int, List[int]],
    by_xero_id: Dict[str, List[int]],
    inv: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    booking_id = int(booking["id"])
    label = format_invoice_label(booking, inv)
    ref = stored_invoice_reference_number(booking)
    if ref is None and inv is not None:
        ref = xero_invoice_reference_number(inv)
    if ref is not None:
        ids = list(by_ref.get(ref) or [])
        if booking_id not in ids:
            ids.append(booking_id)
        if len(ids) > 1:
            return (
                "unmatched invoice={0} booking_id={1} reason=multiple bookings ({2})"
            ).format(label, booking_id, ", ".join(str(i) for i in sorted(ids)))
    xid = str(booking.get("xero_invoice_id") or "").strip()
    if inv and str(inv.get("InvoiceID") or "").strip():
        xid = str(inv.get("InvoiceID") or "").strip() or xid
    if xero.is_real_invoice_id(xid):
        ids = list(by_xero_id.get(xid) or [])
        if booking_id not in ids:
            ids.append(booking_id)
        if len(ids) > 1:
            return (
                "unmatched invoice={0} booking_id={1} reason=multiple bookings share Xero invoice ({2})"
            ).format(label, booking_id, ", ".join(str(i) for i in sorted(ids)))
    return None


def _payment_log_line(
    *,
    invoice_label: str,
    booking_id: Any,
    previous_status: str,
    new_status: str,
    timestamp: str,
    action: str = "updated",
) -> str:
    return (
        "{action} invoice={invoice} booking_id={booking_id} "
        "previous_payment_status={previous} new_payment_status={new} "
        "sync_timestamp={timestamp}"
    ).format(
        action=action,
        invoice=invoice_label,
        booking_id=booking_id,
        previous=previous_status,
        new=new_status,
        timestamp=timestamp,
    )


def sync_booking_payment_from_xero(
    booking: Dict[str, Any],
    *,
    by_ref: Optional[Dict[int, List[int]]] = None,
    by_xero_id: Optional[Dict[str, List[int]]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    One-way sync: Xero fully paid → booking payment_status Paid.

    Never changes Booking Status. Never downgrades a manually marked Paid booking.
    """
    booking_id = int(booking["id"])
    invoice_label = _booking_invoice_label(booking)
    previous_status = invoice.normalize_payment_status(booking.get("payment_status"))
    timestamp = _sync_timestamp()
    result: Dict[str, Any] = {
        "booking_id": booking_id,
        "invoice_label": invoice_label,
        "ok": True,
        "updated": False,
        "skipped": False,
        "unmatched": False,
        "message": "",
        "log_lines": [],
        "previous_payment_status": previous_status,
        "new_payment_status": previous_status,
        "sync_timestamp": timestamp,
    }

    if not xero.is_ready():
        result["ok"] = False
        result["message"] = "Connect Xero in Settings first."
        return result

    if previous_status == invoice.PAYMENT_STATUS_PAID:
        result["skipped"] = True
        result["message"] = "Already Paid."
        return result

    all_bookings = [dict(row) for row in db.list_all()]
    if by_ref is None or by_xero_id is None:
        by_ref, by_xero_id = _index_bookings_by_invoice_keys(all_bookings)

    ambiguous = _ambiguous_match_message(booking, by_ref, by_xero_id)
    if ambiguous:
        result["unmatched"] = True
        result["skipped"] = True
        result["message"] = ambiguous
        result["log_lines"].append(ambiguous)
        return result

    inv, fetch_error = fetch_xero_invoice_for_booking(booking)
    if fetch_error:
        unmatched = fetch_error.startswith("unmatched ")
        result["ok"] = not unmatched
        result["unmatched"] = unmatched
        result["skipped"] = unmatched
        result["message"] = fetch_error
        result["log_lines"].append(fetch_error)
        return result
    if not inv:
        result["skipped"] = True
        result["message"] = "No matching Xero invoice."
        return result

    invoice_label = format_invoice_label(booking, inv)
    result["invoice_label"] = invoice_label

    ambiguous = _ambiguous_match_message(booking, by_ref, by_xero_id, inv)
    if ambiguous:
        result["unmatched"] = True
        result["skipped"] = True
        result["message"] = ambiguous
        result["log_lines"].append(ambiguous)
        return result

    result["log_lines"].append(
        "matched invoice={0} booking_id={1} previous_payment_status={2} "
        "sync_timestamp={3}".format(
            invoice_label,
            booking_id,
            previous_status,
            timestamp,
        )
    )

    if not is_xero_invoice_fully_paid(inv):
        result["skipped"] = True
        xero_status = str(inv.get("Status") or "").strip() or "—"
        result["message"] = "Not fully paid in Xero (status={0}, amount_due={1}).".format(
            xero_status,
            inv.get("AmountDue"),
        )
        return result

    payment_status, paid_at = xero.derive_payment_status_from_invoice(inv, booking)
    if payment_status != invoice.PAYMENT_STATUS_PAID:
        result["skipped"] = True
        result["message"] = "Not fully paid in Xero."
        return result

    if dry_run:
        result["updated"] = False
        result["skipped"] = True
        result["new_payment_status"] = invoice.PAYMENT_STATUS_PAID
        result["message"] = "{0} would be marked Paid from Xero (dry-run).".format(
            invoice_label
        )
        result["log_lines"].append(
            _payment_log_line(
                invoice_label=invoice_label,
                booking_id=booking_id,
                previous_status=previous_status,
                new_status=invoice.PAYMENT_STATUS_PAID,
                timestamp=timestamp,
                action="dry-run",
            )
        )
        return result

    invoice.apply_payment_status(
        booking_id,
        invoice.PAYMENT_STATUS_PAID,
        paid_at=paid_at,
    )

    result["updated"] = True
    result["new_payment_status"] = invoice.PAYMENT_STATUS_PAID
    result["message"] = "{0} marked Paid from Xero.".format(invoice_label)
    result["log_lines"].append(
        _payment_log_line(
            invoice_label=invoice_label,
            booking_id=booking_id,
            previous_status=previous_status,
            new_status=invoice.PAYMENT_STATUS_PAID,
            timestamp=timestamp,
        )
    )
    automation.log_event(
        automation.AUTOMATION_XERO_PAYMENT_SYNC,
        automation.STATUS_SUCCESS,
        result["message"],
        booking_id=booking_id,
    )
    return result


def sync_xero_payments(*, source: str = "manual", dry_run: bool = False) -> Dict[str, Any]:
    """
    Pull paid Xero invoices and mark matching bookings as Paid.

    Used by Dashboard manual sync and Render cron (every 15 minutes).
    """
    started_at = datetime.now(ZoneInfo("UTC")).isoformat()
    log_lines: List[str] = ["Xero payment sync started"]
    if dry_run:
        log_lines.append("dry-run=true (no booking rows will be updated)")
    summary: Dict[str, Any] = {
        "ok": False,
        "message": "",
        "updated": 0,
        "checked": 0,
        "skipped": 0,
        "unmatched": 0,
        "errors": [],
        "log_lines": log_lines,
        "source": source,
        "dry_run": dry_run,
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
    unmatched = 0
    errors: List[Dict[str, Any]] = []
    bookings = [dict(row) for row in db.list_all()]
    by_ref, by_xero_id = _index_bookings_by_invoice_keys(bookings)
    logged_unmatched_keys = set()

    for booking in bookings:
        if not _booking_eligible_for_sync(booking):
            continue
        checked += 1
        try:
            outcome = sync_booking_payment_from_xero(
                booking,
                by_ref=by_ref,
                by_xero_id=by_xero_id,
                dry_run=dry_run,
            )
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

        for line in outcome.get("log_lines") or []:
            if line.startswith("unmatched "):
                if line in logged_unmatched_keys:
                    continue
                logged_unmatched_keys.add(line)
            log_lines.append(line)

        if outcome.get("unmatched"):
            unmatched += 1
            skipped += 1
        elif not outcome.get("ok"):
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
    summary["unmatched"] = unmatched
    summary["errors"] = errors

    log_lines.append("{0} invoices checked".format(checked))
    log_lines.append("{0} booking(s) updated".format(updated))
    log_lines.append("{0} unmatched".format(unmatched))
    if errors:
        summary["message"] = "Synced {0} payment(s); {1} error(s).".format(
            updated,
            len(errors),
        )
        event_status = automation.STATUS_PARTIAL
    elif updated:
        summary["message"] = "Synced {0} payment(s) from Xero.".format(updated)
        event_status = automation.STATUS_SUCCESS
    else:
        summary["message"] = "No new paid invoices to sync from Xero."
        event_status = automation.STATUS_SUCCESS
    if not dry_run:
        automation.log_event(
            automation.AUTOMATION_XERO_PAYMENT_SYNC,
            event_status,
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
        "last_unmatched": unmatched,
        "last_log_lines": log_lines,
    }
    if not errors:
        state["last_success_at"] = finished_at
    if not dry_run:
        save_sync_state(state)

    for line in log_lines:
        logger.info(line)

    return summary
