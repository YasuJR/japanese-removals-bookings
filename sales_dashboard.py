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


def _is_paid(booking: Dict[str, Any]) -> bool:
    return (
        invoice.normalize_payment_status(booking.get("payment_status"))
        == invoice.PAYMENT_STATUS_PAID
    )


def _is_cancelled(booking: Dict[str, Any]) -> bool:
    return job_status.normalize(booking.get("status")) == "Cancelled"


def _invoice_total(booking: Dict[str, Any]) -> float:
    return round(float(invoice.calculate_invoice_totals(booking)["total"]), 2)


def _period_sales(
    paid_rows: List[Tuple[int, float, date]], start: date, end: date
) -> Tuple[float, int]:
    total = 0.0
    count = 0
    seen = set()
    for booking_id, amount, paid_on in paid_rows:
        if booking_id in seen:
            continue
        if start <= paid_on <= end:
            seen.add(booking_id)
            total += amount
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

    rows = [dict(row) for row in db.list_all()]
    db.attach_extra_charges(rows)

    paid_rows: List[Tuple[int, float, date]] = []
    unpaid_total = 0.0
    unpaid_count = 0
    seen_ids = set()

    for row in rows:
        try:
            booking_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if booking_id in seen_ids:
            continue
        seen_ids.add(booking_id)

        if _is_paid(row):
            paid_on = paid_on_perth(row)
            if paid_on is None:
                continue
            paid_rows.append((booking_id, _invoice_total(row), paid_on))
            continue

        if _is_cancelled(row):
            continue
        if not has_invoice(row):
            continue
        unpaid_total += _invoice_total(row)
        unpaid_count += 1

    today_sales, today_jobs = _period_sales(paid_rows, today, today)
    week_sales, week_jobs = _period_sales(paid_rows, week_start, week_end)
    month_sales, month_jobs = _period_sales(paid_rows, month_start, month_end)
    fy_sales, fy_jobs = _period_sales(paid_rows, fy_start, fy_end)

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
        "unpaid_amount": round(unpaid_total, 2),
        "unpaid_count": unpaid_count,
        "average_job_value": _average_job_value(fy_sales, fy_jobs),
        "average_job_count": fy_jobs,
    }
