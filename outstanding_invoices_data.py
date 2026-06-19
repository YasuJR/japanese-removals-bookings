"""Outstanding invoice dashboard — unpaid, overdue, and paid tracking."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import config
import database as db
import invoice
from integrations import xero

INVOICE_FILTERS = [
    ("unpaid", "Unpaid"),
    ("overdue", "Overdue"),
    ("paid", "Paid"),
]


def _parse_iso_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def has_invoice(booking: Dict[str, Any]) -> bool:
    if xero.is_real_invoice_id(booking.get("xero_invoice_id")):
        return True
    if (booking.get("invoice_number") or "").strip():
        return True
    status = (booking.get("invoice_status") or "").strip()
    return status in ("DRAFT", "Draft Created", "AUTHORISED", "SUBMITTED", "PAID")


def issue_date_for(booking: Dict[str, Any]) -> str:
    stored = (booking.get("invoice_issue_date") or "").strip()
    if stored:
        return stored[:10]
    return (booking.get("move_date") or "").strip()[:10]


def due_date_for(booking: Dict[str, Any]) -> str:
    stored = (booking.get("invoice_due_date") or "").strip()
    if stored:
        return stored[:10]
    issue = _parse_iso_date(issue_date_for(booking))
    if issue is None:
        return ""
    due = issue + timedelta(days=config.INVOICE_DUE_DAYS)
    return due.isoformat()


def _is_paid(booking: Dict[str, Any]) -> bool:
    return (
        invoice.normalize_payment_status(booking.get("payment_status"))
        == invoice.PAYMENT_STATUS_PAID
    )


def _is_overdue(booking: Dict[str, Any], today: date) -> bool:
    status = invoice.normalize_payment_status(booking.get("payment_status"))
    if status == invoice.PAYMENT_STATUS_PAID:
        return False
    if status == invoice.PAYMENT_STATUS_OVERDUE:
        return True
    due = _parse_iso_date(due_date_for(booking))
    if due is None:
        return False
    return due < today


def days_overdue(booking: Dict[str, Any], today: date) -> Optional[int]:
    if not _is_overdue(booking, today):
        return None
    due = _parse_iso_date(due_date_for(booking))
    if due is None:
        return None
    return (today - due).days


def invoice_number_display(booking: Dict[str, Any]) -> str:
    number = (booking.get("invoice_number") or "").strip()
    if number:
        return number
    xid = (booking.get("xero_invoice_id") or "").strip()
    if xid:
        return xid[:12]
    return "Booking #{0}".format(booking.get("id", ""))


def _invoice_row(booking: Dict[str, Any], today: date) -> Dict[str, Any]:
    totals = invoice.calculate_invoice_totals(booking)
    overdue_days = days_overdue(booking, today)
    xid = (booking.get("xero_invoice_id") or "").strip()
    return {
        "booking_id": int(booking["id"]),
        "invoice_number": invoice_number_display(booking),
        "customer_name": booking.get("customer_name") or "",
        "phone": (booking.get("phone") or "").strip(),
        "email": (booking.get("email") or "").strip(),
        "amount": totals["total"],
        "issue_date": issue_date_for(booking),
        "due_date": due_date_for(booking),
        "days_overdue": overdue_days,
        "days_overdue_label": str(overdue_days) if overdue_days is not None else "—",
        "payment_status": invoice.normalize_payment_status(
            booking.get("payment_status")
        ),
        "xero_invoice_id": xid,
        "xero_url": xero.invoice_url(xid) if xero.is_real_invoice_id(xid) else "",
        "paid_at": (booking.get("paid_at") or "").strip(),
    }


def _payment_days(booking: Dict[str, Any]) -> Optional[int]:
    if not _is_paid(booking):
        return None
    paid = _parse_iso_date((booking.get("paid_at") or "")[:10])
    issued = _parse_iso_date(issue_date_for(booking))
    if paid is None or issued is None:
        return None
    return max((paid - issued).days, 0)


def _month_bounds(today: date) -> tuple:
    first = today.replace(day=1)
    last_day = monthrange(today.year, today.month)[1]
    last = today.replace(day=last_day)
    return first, last


def build_outstanding_dashboard(
    filter_name: str = "unpaid",
    today: Optional[date] = None,
) -> Dict[str, Any]:
    if today is None:
        today = date.today()

    active_filter = (filter_name or "unpaid").strip().lower()
    if active_filter not in {k for k, _ in INVOICE_FILTERS}:
        active_filter = "unpaid"

    invoiced = [
        dict(row) for row in db.list_all() if has_invoice(dict(row))
    ]

    unpaid_rows = [b for b in invoiced if not _is_paid(b)]
    overdue_rows = [b for b in unpaid_rows if _is_overdue(b, today)]
    paid_rows = [b for b in invoiced if _is_paid(b)]

    outstanding_total = sum(
        invoice.calculate_invoice_totals(b)["total"] for b in unpaid_rows
    )
    overdue_total = sum(
        invoice.calculate_invoice_totals(b)["total"] for b in overdue_rows
    )

    month_start, month_end = _month_bounds(today)
    paid_this_month = []
    payment_day_samples = []
    for booking in paid_rows:
        paid_on = _parse_iso_date((booking.get("paid_at") or "")[:10])
        if paid_on and month_start <= paid_on <= month_end:
            paid_this_month.append(booking)
        days = _payment_days(booking)
        if days is not None and paid_on and month_start <= paid_on <= month_end:
            payment_day_samples.append(days)

    paid_month_total = sum(
        invoice.calculate_invoice_totals(b)["total"] for b in paid_this_month
    )
    avg_payment_days = (
        round(sum(payment_day_samples) / len(payment_day_samples), 1)
        if payment_day_samples
        else None
    )

    if active_filter == "overdue":
        filtered_bookings = overdue_rows
    elif active_filter == "paid":
        filtered_bookings = paid_rows
    else:
        filtered_bookings = unpaid_rows

    rows = [_invoice_row(b, today) for b in filtered_bookings]

    if active_filter == "overdue":
        rows.sort(
            key=lambda r: (
                -(r["days_overdue"] or 0),
                r["due_date"],
                r["booking_id"],
            )
        )
    elif active_filter == "paid":
        rows.sort(key=lambda r: (r.get("paid_at") or "", r["booking_id"]), reverse=True)
    else:
        rows.sort(key=lambda r: (r["due_date"], r["booking_id"]))

    return {
        "today": today.isoformat(),
        "active_filter": active_filter,
        "summary": {
            "outstanding_total": round(outstanding_total, 2),
            "outstanding_count": len(unpaid_rows),
            "overdue_total": round(overdue_total, 2),
            "overdue_count": len(overdue_rows),
            "paid_month_total": round(paid_month_total, 2),
            "paid_month_count": len(paid_this_month),
            "avg_payment_days": avg_payment_days,
            "invoice_count": len(rows),
        },
        "invoices": rows,
    }
