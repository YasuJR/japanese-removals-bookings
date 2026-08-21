"""Invoice calculations and local Xero draft placeholder (no API yet)."""

import html
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

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


def invoice_customer_bill_to(booking: Any) -> Dict[str, str]:
    """
    BILL TO block for Invoice preview and PDF.

    Uses stored booking pickup_address and delivery_address (drop-off).
    Does not include phone or email, and does not fall back to company contact.
    """
    if booking is None:
        row: Dict[str, Any] = {}
    elif isinstance(booking, dict):
        row = booking
    elif hasattr(booking, "keys"):
        row = dict(booking)
    else:
        row = {}
    pickup = str(row.get("pickup_address") or "").strip()
    dropoff = ""
    for key in ("delivery_address", "dropoff_address", "destination_address"):
        dropoff = str(row.get(key) or "").strip()
        if dropoff:
            break
    pickup_line = "Pickup: {0}".format(pickup).rstrip()
    dropoff_line = "Drop-off: {0}".format(dropoff).rstrip()
    return {
        "customer_name": str(row.get("customer_name") or "").strip(),
        "pickup_address": pickup,
        "dropoff_address": dropoff,
        "pickup_line": pickup_line,
        "dropoff_line": dropoff_line,
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
    """Format money as $1,234.56 or -$143.50."""
    value = round(float(amount), 2)
    if value == 0:
        return "$0.00"
    formatted = "${0:,.2f}".format(abs(value))
    if value < 0:
        return "-" + formatted
    return formatted


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


def normalize_invoice_description(value: Any) -> str:
    """Preserve internal newlines; trim surrounding whitespace."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.strip("\n").rstrip()
    if len(text) > 2000:
        return text[:2000]
    return text


def stored_invoice_description(booking: Dict[str, Any]) -> str:
    """Saved custom labour description, or empty when using auto-generated text."""
    return normalize_invoice_description(booking.get("invoice_description"))


def resolve_labour_description(
    booking: Dict[str, Any], totals: Optional[Dict[str, Any]] = None
) -> str:
    """
    Customer-facing labour line description.

    Uses a saved Invoice Description when present; otherwise the auto-generated
    Moving Labour text. Existing bookings with a NULL/blank value keep the
    current auto-generated fallback.
    """
    stored = stored_invoice_description(booking)
    if stored:
        return stored
    if totals is None:
        totals = calculate_invoice_totals(booking)
    return format_moving_labour_description(booking, totals)


def invoice_description_markup(
    booking: Dict[str, Any], totals: Optional[Dict[str, Any]] = None
) -> str:
    """HTML/ReportLab markup for preview and PDF (escaped, newlines as <br/>)."""
    return plain_text_to_invoice_markup(resolve_labour_description(booking, totals))


def plain_text_to_invoice_markup(text: str) -> str:
    normalized = normalize_invoice_description(text)
    escaped = html.escape(normalized, quote=False)
    return escaped.replace("\n", "<br/>")


def stored_description_for_save(data: Dict[str, Any]) -> str:
    """
    Persist a custom description only when the user edited it.

    invoice_description_custom=1 keeps the submitted text (including after
    later time/rate changes). Flag 0 or a blank value stores empty so the
    auto-generated description remains the fallback.
    """
    submitted = normalize_invoice_description(data.get("invoice_description"))
    flag = str(data.get("invoice_description_custom") or "").strip()
    if flag == "0":
        return ""
    if flag == "1":
        return submitted
    if not submitted:
        return ""
    auto = format_moving_labour_description(data, calculate_invoice_totals(data))
    if submitted == auto:
        return ""
    return submitted


def invoice_description_form_values(booking: Dict[str, Any]) -> Dict[str, str]:
    """Textarea value and custom flag for the Edit Booking form."""
    stored = stored_invoice_description(booking)
    if stored:
        return {
            "invoice_description": stored,
            "invoice_description_custom": "1",
        }
    totals = calculate_invoice_totals(booking)
    return {
        "invoice_description": format_moving_labour_description(booking, totals),
        "invoice_description_custom": "0",
    }


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
        complete_job_when_payment_paid(booking_id)
    return True, "Payment status updated to {0}.".format(normalized)


def complete_job_when_payment_paid(booking_id: int) -> bool:
    """
    When Payment is Paid, set Job Status to Completed.

    Leaves Cancelled and already-Completed jobs unchanged. Does not run when
    Payment is Unpaid / Part Paid / Overdue, and does not revert Completed
    if Payment is later changed back.
    """
    import job_status

    row = db.get_booking(booking_id)
    if not row:
        return False
    if normalize_payment_status(row["payment_status"]) != PAYMENT_STATUS_PAID:
        return False
    current = job_status.normalize(row["status"])
    if current in ("Completed", "Cancelled"):
        return False
    db.update_booking_status(booking_id, "Completed")
    return True


def set_payment_status(booking_id: int, paid: bool) -> Tuple[bool, str]:
    status = PAYMENT_STATUS_PAID if paid else PAYMENT_STATUS_UNPAID
    paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if paid else ""
    return apply_payment_status(booking_id, status, paid_at=paid_at)


def default_invoice_fields() -> Dict[str, Any]:
    return company_config.default_invoice_fields()
