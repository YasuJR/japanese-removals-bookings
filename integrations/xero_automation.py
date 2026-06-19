"""Xero invoice automation: create draft → approve → email."""

from typing import Any, Dict, Optional, Tuple

import automation
import database as db
import job_status
from integrations import xero


def _require_ready(booking: Dict[str, Any]) -> Optional[str]:
    if not xero.is_configured():
        return "Xero is not configured — open Settings → Xero."
    if not xero.is_connected():
        return "Xero is not connected."
    if not xero.is_ready():
        return "Set Tenant ID on the Xero settings page."
    if xero.is_real_invoice_id(booking.get("xero_invoice_id")):
        status = xero.resolve_invoice_status(booking)
        if status in ("AUTHORISED", "PAID"):
            return "Invoice already {0} for this booking.".format(status.title())
    email = (booking.get("email") or "").strip()
    if not email:
        return "Customer email is required to email the invoice."
    return None


def create_approve_and_email_invoice(
    booking: Dict[str, Any],
) -> Tuple[bool, str, str]:
    """
    Full automation flow. Returns (ok, message, xero_invoice_url).
    """
    booking_id = int(booking["id"])
    err = _require_ready(booking)
    if err:
        automation.log_event(
            automation.AUTOMATION_XERO_INVOICE_EMAIL,
            automation.STATUS_ERROR,
            err,
            booking_id=booking_id,
        )
        return False, err, ""

    steps = []

    ok, msg, inv = xero.sync_invoice_record(booking, confirm_new=False)
    steps.append("Create draft: {0}".format(msg))
    if not ok or not inv:
        automation.log_event(
            automation.AUTOMATION_XERO_INVOICE_EMAIL,
            automation.STATUS_ERROR,
            " · ".join(steps),
            booking_id=booking_id,
        )
        return False, msg, ""

    invoice_id = inv.get("InvoiceID") or ""
    if not invoice_id:
        automation.log_event(
            automation.AUTOMATION_XERO_INVOICE_EMAIL,
            automation.STATUS_ERROR,
            "Draft created but missing invoice ID.",
            booking_id=booking_id,
        )
        return False, "Draft created but missing invoice ID.", ""

    ok, msg, inv = xero.approve_invoice(invoice_id)
    steps.append("Approve: {0}".format(msg))
    if not ok:
        automation.log_event(
            automation.AUTOMATION_XERO_INVOICE_EMAIL,
            automation.STATUS_PARTIAL,
            " · ".join(steps),
            booking_id=booking_id,
        )
        return False, msg, xero.invoice_url(invoice_id)

    if inv:
        xero.persist_invoice_from_xero(booking_id, inv)

    ok, msg = xero.email_invoice(invoice_id)
    steps.append("Email: {0}".format(msg))
    if not ok:
        automation.log_event(
            automation.AUTOMATION_XERO_INVOICE_EMAIL,
            automation.STATUS_PARTIAL,
            " · ".join(steps),
            booking_id=booking_id,
        )
        return (
            False,
            "Invoice approved but email failed: {0}".format(msg),
            xero.invoice_url(invoice_id),
        )

    db.update_booking_status(booking_id, "Invoiced")

    number = (inv or {}).get("InvoiceNumber") or invoice_id[:8]
    customer_email = (booking.get("email") or "").strip()
    success_msg = (
        "Invoice {0} created, approved, and emailed to {1}."
    ).format(number, customer_email)
    automation.log_event(
        automation.AUTOMATION_XERO_INVOICE_EMAIL,
        automation.STATUS_SUCCESS,
        success_msg,
        booking_id=booking_id,
    )
    return True, success_msg, xero.invoice_url(invoice_id)


def auto_create_invoice_on_pending_confirmed(
    booking: Dict[str, Any],
    previous_status: str,
) -> Optional[str]:
    """
    Phase 7 — when status changes Pending → Confirmed, create and approve
    a Xero invoice once (skip if already linked).
    """
    booking_id = int(booking["id"])
    current = job_status.display(booking)
    if current != "Confirmed":
        return None
    if job_status.normalize(previous_status) != "Pending":
        return None

    if xero.is_real_invoice_id(booking.get("xero_invoice_id")):
        db.update_booking_invoice_fields(
            booking_id, {"xero_invoice_automation_error": ""}
        )
        automation.log_event(
            automation.AUTOMATION_XERO_INVOICE_AUTO_CREATE,
            automation.STATUS_PARTIAL,
            "Xero invoice already linked — skipped duplicate create.",
            booking_id,
        )
        return None

    if not xero.is_ready():
        err = "Xero invoice auto-create skipped — connect Xero in Settings."
        db.update_booking_invoice_fields(
            booking_id, {"xero_invoice_automation_error": err}
        )
        automation.log_event(
            automation.AUTOMATION_XERO_INVOICE_AUTO_CREATE,
            automation.STATUS_ERROR,
            err,
            booking_id,
        )
        return err

    ok, msg, inv = xero.create_and_authorise_invoice_for_booking(booking)
    if ok and inv:
        db.update_booking_invoice_fields(
            booking_id, {"xero_invoice_automation_error": ""}
        )
        number = (inv.get("InvoiceNumber") or "").strip()
        success = msg or (
            "Xero invoice {0} created and approved.".format(number or "created")
        )
        automation.log_event(
            automation.AUTOMATION_XERO_INVOICE_AUTO_CREATE,
            automation.STATUS_SUCCESS,
            success,
            booking_id,
        )
        return success

    err = msg or "Xero invoice auto-create failed."
    db.update_booking_invoice_fields(
        booking_id, {"xero_invoice_automation_error": err}
    )
    automation.log_event(
        automation.AUTOMATION_XERO_INVOICE_AUTO_CREATE,
        automation.STATUS_ERROR,
        err,
        booking_id,
    )
    return "Xero invoice auto-create failed: {0}".format(err)
