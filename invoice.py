"""Invoice calculations and local Xero draft placeholder (no API yet)."""

from datetime import datetime
from typing import Any, Dict, List, Tuple

import config
import database as db
import booking_profit
import invoice_numbering
from booking_times import (
    effective_duration_hours,
    format_time_12h,
    normalize_time_input,
)
from extra_charges import charge_line_total, charges_gross_total
from integrations import company_config

GST_RATE = 0.10
PAYMENT_STATUS_UNPAID = "Unpaid"
PAYMENT_STATUS_PAID = "Paid"
PAYMENT_STATUS_PART_PAID = "Part Paid"
PAYMENT_STATUS_OVERDUE = "Overdue"

PAYMENT_STATUS_OPTIONS = (
    PAYMENT_STATUS_UNPAID,
    PAYMENT_STATUS_PAID,
    PAYMENT_STATUS_PART_PAID,
    PAYMENT_STATUS_OVERDUE,
)

# Quick payment changes from the Dashboard jobs table.
DASHBOARD_INLINE_PAYMENT_OPTIONS = (
    PAYMENT_STATUS_UNPAID,
    PAYMENT_STATUS_PAID,
)


def _duration_hours(booking: Dict[str, Any]) -> float:
    return effective_duration_hours(booking, default=1.0)


def _money(value: float) -> float:
    return round(float(value), 2)


def resolve_booking_invoice(booking: Dict[str, Any]) -> Dict[str, Any]:
    """Apply config defaults when invoice fields are unset on the row."""
    defaults = default_invoice_fields()
    out = dict(booking)
    if out.get("hourly_rate") in (None, ""):
        out["hourly_rate"] = defaults["hourly_rate"]
    if out.get("callout_fee") in (None, ""):
        out["callout_fee"] = defaults["callout_fee"]
    if out.get("gst_enabled") in (None, ""):
        out["gst_enabled"] = defaults["gst_enabled"]
    if not (out.get("payment_status") or "").strip():
        out["payment_status"] = defaults["payment_status"]
    if "extra_charges" not in out and out.get("id"):
        out["extra_charges"] = db.list_extra_charges(int(out["id"]))
    elif "extra_charges" not in out:
        out["extra_charges"] = []
    return out


def calculate_invoice_totals(booking: Dict[str, Any]) -> Dict[str, Any]:
    """
    Invoice totals from booking fields.

    Prices are GST-inclusive by default when GST is enabled.
    Extra charges are included in the same pricing mode.
    """
    booking = resolve_booking_invoice(booking)
    hourly_rate = float(booking.get("hourly_rate") or 0)
    callout_fee = float(booking.get("callout_fee") or 0)
    hours = _duration_hours(booking)
    gst_enabled = bool(int(booking.get("gst_enabled") or 0))
    extra_items: List[Dict[str, Any]] = list(booking.get("extra_charges") or [])
    extras_gross = charges_gross_total(extra_items)
    labour_gross = _money((hourly_rate * hours) + callout_fee)
    gross = _money(labour_gross + extras_gross)

    if gst_enabled and company_config.gst_pricing_inclusive():
        total = gross
        subtotal = _money(gross / (1 + GST_RATE))
        gst_amount = _money(total - subtotal)
    elif gst_enabled:
        subtotal = gross
        gst_amount = _money(subtotal * GST_RATE)
        total = _money(subtotal + gst_amount)
    else:
        subtotal = gross
        gst_amount = 0.0
        total = gross

    return {
        "hourly_rate": hourly_rate,
        "callout_fee": callout_fee,
        "hours": hours,
        "gst_enabled": gst_enabled,
        "extra_charges": extra_items,
        "extras_total": extras_gross,
        "labour_gross": labour_gross,
        "subtotal": subtotal,
        "net_sales": subtotal,
        "gst_amount": gst_amount,
        "total": total,
    }


def invoice_summary(booking: Dict[str, Any]) -> Dict[str, Any]:
    """Totals plus status fields for templates."""
    totals = calculate_invoice_totals(booking)
    displayed = invoice_numbering.display_invoice_number(booking)
    return {
        **totals,
        "payment_status": normalize_payment_status(booking.get("payment_status")),
        "invoice_status": (booking.get("invoice_status") or "").strip() or "—",
        "invoice_number": "" if displayed == "—" else displayed,
        "xero_invoice_id": (booking.get("xero_invoice_id") or "").strip(),
    }


def calculate_from_form_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Invoice totals from parsed form pricing fields (no DB read)."""
    booking = {
        "hourly_rate": data.get("hourly_rate", 0),
        "callout_fee": data.get("callout_fee", 0),
        "duration_hours": data.get("duration_hours"),
        "start_time": data.get("start_time", ""),
        "finish_time": data.get("finish_time", ""),
        "gst_enabled": data.get("gst_enabled", 0),
        "extra_charges": data.get("extra_charges") or [],
    }
    return calculate_invoice_totals(booking)


def format_aud(amount: float) -> str:
    return "${0:,.2f}".format(amount)


def _format_labour_hours(hours: float) -> str:
    rounded = round(float(hours), 2)
    if rounded == int(rounded):
        return str(int(rounded))
    text = "{0:.2f}".format(rounded)
    return text.rstrip("0").rstrip(".")


def format_moving_labour_description(
    booking: Dict[str, Any], totals: Dict[str, Any]
) -> str:
    """
    Moving Labour line for invoice preview, PDF, and Xero.

    Uses stored start/finish times when both are set — never derives finish from duration.
    """
    hours_text = _format_labour_hours(totals["hours"])
    rate = format_aud(totals["hourly_rate"])
    start = normalize_time_input(booking.get("start_time"))
    finish = normalize_time_input(booking.get("finish_time"))
    if start and finish:
        time_range = "{0} - {1}".format(format_time_12h(start), format_time_12h(finish))
        return "Moving Labour — {0} — {1} hrs @ {2}/hr".format(
            time_range, hours_text, rate
        )
    return "Moving Labour — {0} hrs @ {1}/hr".format(hours_text, rate)


def normalize_payment_status(value: Any) -> str:
    text = str(value or "").strip()
    if text in PAYMENT_STATUS_OPTIONS:
        return text
    return PAYMENT_STATUS_UNPAID


def payment_status_css(value: Any) -> str:
    return normalize_payment_status(value).lower().replace(" ", "-")


def validate_dashboard_inline_payment(value: Any) -> str:
    text = str(value or "").strip()
    if text in DASHBOARD_INLINE_PAYMENT_OPTIONS:
        return text
    return ""


def apply_payment_status(
    booking_id: int,
    status: str,
    *,
    paid_at: str = "",
) -> Tuple[bool, str]:
    """Set payment status from Xero sync or manual update."""
    normalized = normalize_payment_status(status)
    fields: Dict[str, Any] = {"payment_status": normalized}
    if normalized == PAYMENT_STATUS_PAID:
        fields["paid_at"] = paid_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        fields["paid_at"] = paid_at if paid_at else ""
    db.update_booking_invoice_fields(booking_id, fields)
    if normalized == PAYMENT_STATUS_PAID:
        booking_profit.recalculate_and_save(booking_id)
    return True, "Payment status updated to {0}.".format(normalized)


def set_payment_status(booking_id: int, paid: bool) -> Tuple[bool, str]:
    status = PAYMENT_STATUS_PAID if paid else PAYMENT_STATUS_UNPAID
    paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if paid else ""
    return apply_payment_status(booking_id, status, paid_at=paid_at)


def default_invoice_fields() -> Dict[str, Any]:
    return company_config.default_invoice_fields()
