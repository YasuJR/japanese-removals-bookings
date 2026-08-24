"""Sales summary for the staff Dashboard (Paid invoices, Perth / AU FY)."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import config
import database as db
import invoice
import job_status
from outstanding_invoices_data import has_invoice

PERTH_TZ = ZoneInfo(config.TIMEZONE)


def australian_financial_year(today: date) -> Tuple[date, date]:
    """Australia FY: 1 July – 30 June containing today."""
    if today.month >= 7:
        start = date(today.year, 7, 1)
        end = date(today.year + 1, 6, 30)
    else:
        start = date(today.year - 1, 7, 1)
        end = date(today.year, 6, 30)
    return start, end


def month_range(today: date) -> Tuple[date, date]:
    last_day = monthrange(today.year, today.month)[1]
    return today.replace(day=1), today.replace(day=last_day)


def week_range(today: date) -> Tuple[date, date]:
    """Monday–Sunday (ISO week) containing today (Perth calendar date)."""
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _parse_iso_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def paid_on_perth(booking: Dict[str, Any]) -> Optional[date]:
    """
    Calendar date in Australia/Perth when this invoice became Paid.

    Datetimes without a timezone are treated as UTC (Render / Stripe).
    Date-only values (YYYY-MM-DD) are used as-is. Paid rows with no paid_at
    fall back to move_date so legacy Paid invoices still count once.
    """
    raw = booking.get("paid_at")
    if raw is None or str(raw).strip() == "":
        return _parse_iso_date(booking.get("move_date"))

    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(PERTH_TZ).date()

    if isinstance(raw, date):
        return raw

    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) == 10:
        return _parse_iso_date(text)

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(text[:19], fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return _parse_iso_date(text)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PERTH_TZ).date()


def is_paid_invoice(booking: Dict[str, Any]) -> bool:
    return (
        invoice.normalize_payment_status(booking.get("payment_status"))
        == invoice.PAYMENT_STATUS_PAID
    )


def is_cancelled_booking(booking: Dict[str, Any]) -> bool:
    return job_status.normalize(booking.get("status")) == "Cancelled"


def invoice_total(booking: Dict[str, Any]) -> float:
    return round(float(invoice.calculate_invoice_totals(booking)["total"]), 2)


def booking_move_date(booking: Dict[str, Any]) -> Optional[date]:
    return _parse_iso_date(booking.get("move_date"))


def load_unique_bookings() -> List[Dict[str, Any]]:
    """All bookings, unique by id, with extra charges attached. Read-only."""
    rows: List[Dict[str, Any]] = []
    seen = set()
    for raw in db.list_all():
        row = dict(raw)
        try:
            booking_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if booking_id in seen:
            continue
        seen.add(booking_id)
        rows.append(row)
    db.attach_extra_charges(rows)
    return rows


def paid_bookings_in_period(
    start: date,
    end: date,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Paid invoices whose Perth paid_at (or fallback) falls in [start, end]."""
    if rows is None:
        rows = load_unique_bookings()
    matched: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        try:
            booking_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if booking_id in seen:
            continue
        if not is_paid_invoice(row):
            continue
        paid_on = paid_on_perth(row)
        if paid_on is None or not (start <= paid_on <= end):
            continue
        seen.add(booking_id)
        matched.append(row)
    return matched


def paid_sales_in_period(
    start: date,
    end: date,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[float, int]:
    """Invoice-total sum and job count for Paid invoices in the period."""
    bookings = paid_bookings_in_period(start, end, rows)
    total = round(sum(invoice_total(row) for row in bookings), 2)
    return total, len(bookings)


def outstanding_invoices(
    rows: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[float, int]:
    """
    Unpaid invoice total: has an invoice, not Paid, not Cancelled.

    Not period-filtered. Paid invoices are never included.
    """
    if rows is None:
        rows = load_unique_bookings()
    total = 0.0
    count = 0
    seen = set()
    for row in rows:
        try:
            booking_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if booking_id in seen:
            continue
        seen.add(booking_id)
        if is_paid_invoice(row) or is_cancelled_booking(row):
            continue
        if not has_invoice(row):
            continue
        total += invoice_total(row)
        count += 1
    return round(total, 2), count


def _average_job_value(sales: float, jobs: int) -> float:
    if jobs <= 0:
        return 0.0
    return round(float(sales) / float(jobs), 2)


def build_sales_summary(today: Optional[date] = None) -> Dict[str, Any]:
    """
    Read-only sales figures from existing bookings.

    Sales = invoice total of each Paid booking, counted once by booking id.
    Bank Transfer / Stripe / Xero / manual Paid all use payment_status = Paid.
    """
    if today is None:
        from dashboard_data import perth_today

        today = perth_today()

    week_start, week_end = week_range(today)
    month_start, month_end = month_range(today)
    fy_start, fy_end = australian_financial_year(today)
    rows = load_unique_bookings()

    today_sales, today_jobs = paid_sales_in_period(today, today, rows)
    week_sales, week_jobs = paid_sales_in_period(week_start, week_end, rows)
    month_sales, month_jobs = paid_sales_in_period(month_start, month_end, rows)
    fy_sales, fy_jobs = paid_sales_in_period(fy_start, fy_end, rows)
    unpaid_total, unpaid_count = outstanding_invoices(rows)

    return {
        "today": today.isoformat(),
        "today_sales": today_sales,
        "today_paid_jobs": today_jobs,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "week_sales": week_sales,
        "week_paid_jobs": week_jobs,
        "month_start": month_start.isoformat(),
        "month_end": month_end.isoformat(),
        "month_sales": month_sales,
        "month_paid_jobs": month_jobs,
        "fy_start": fy_start.isoformat(),
        "fy_end": fy_end.isoformat(),
        "fy_sales": fy_sales,
        "fy_paid_jobs": fy_jobs,
        "unpaid_amount": unpaid_total,
        "unpaid_count": unpaid_count,
        "average_job_value": _average_job_value(fy_sales, fy_jobs),
        "average_job_count": fy_jobs,
    }
