"""Phase 15 — per-booking estimated profit calculation and monthly aggregates."""

from calendar import monthrange
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import database as db
import invoice
import job_status
import sales_dashboard

JOB_COST_FIELDS = (
    "staff_cost",
    "fuel_cost",
    "truck_cost",
    "parking_cost",
    "other_costs",
)

JOB_COST_FIELD_LABELS = {
    "staff_cost": "Staff Cost",
    "fuel_cost": "Fuel Cost",
    "truck_cost": "Truck Cost",
    "parking_cost": "Parking Cost",
    "other_costs": "Other Cost",
}

PROFIT_STATUS_FILTERS = [
    ("all", "All statuses"),
    ("Confirmed", "Confirmed"),
    ("On Route", "On Route"),
    ("Paid", "Paid"),
    ("Completed", "Completed"),
    ("Invoiced", "Invoiced"),
]


def _money(value: float) -> float:
    return round(float(value), 2)


def _float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def resolve_staff_cost(booking: Dict[str, Any]) -> float:
    """Manual staff cost, or crew hours × hourly wage when both are set."""
    hours = booking.get("profit_crew_hours")
    wage = booking.get("profit_hourly_wage")
    if hours not in (None, "", 0) and wage not in (None, "", 0):
        return _money(_float(hours) * _float(wage))
    return _money(_float(booking.get("staff_cost")))


def resolve_stripe_fee(booking: Dict[str, Any]) -> float:
    """Stripe surcharge when the booking was paid by card via Stripe."""
    intent = (booking.get("stripe_payment_intent_id") or "").strip()
    session = (booking.get("stripe_checkout_session_id") or "").strip()
    stripe_status = (booking.get("stripe_payment_status") or "").strip().lower()
    if intent or session or stripe_status == "paid":
        return _money(_float(booking.get("stripe_surcharge_amount")))
    return 0.0


def calculate_gst_amount(total: float, gst_enabled: bool) -> float:
    if not gst_enabled or total <= 0:
        return 0.0
    return _money(total / 11.0)


def calculate_booking_profit(booking: Dict[str, Any]) -> Dict[str, Any]:
    """Compute revenue, costs, estimated profit, and margin for one booking."""
    resolved = invoice.resolve_booking_invoice(booking)
    totals = invoice.calculate_invoice_totals(resolved)
    revenue = _money(totals["total"])
    gst_enabled = bool(int(resolved.get("gst_enabled") or 0))
    gst_amount = calculate_gst_amount(revenue, gst_enabled)
    net_revenue = _money(revenue - gst_amount)
    stripe_fee = resolve_stripe_fee(resolved)
    staff_cost = resolve_staff_cost(resolved)
    fuel_cost = _money(_float(resolved.get("fuel_cost")))
    truck_cost = _money(_float(resolved.get("truck_cost")))
    parking_cost = _money(_float(resolved.get("parking_cost")))
    other_costs = _money(_float(resolved.get("other_costs")))
    total_job_cost = _money(
        staff_cost + fuel_cost + truck_cost + parking_cost + other_costs
    )
    # Dashboard Actual/Projected Costs use Total Job Cost (not Stripe).
    total_costs = total_job_cost
    estimated_profit = _money(net_revenue - total_job_cost)
    margin = net_margin_percent(estimated_profit, net_revenue)
    return {
        "revenue": revenue,
        "gst_amount": gst_amount,
        "net_revenue": net_revenue,
        "stripe_fee": stripe_fee,
        "staff_cost": staff_cost,
        "fuel_cost": fuel_cost,
        "truck_cost": truck_cost,
        "parking_cost": parking_cost,
        "other_costs": other_costs,
        "total_job_cost": total_job_cost,
        "total_costs": total_costs,
        "estimated_profit": estimated_profit,
        "profit_margin_percent": margin,
    }


def profit_margin_percent(revenue: float, estimated_profit: float) -> float:
    if revenue <= 0:
        return 0.0
    return _money(estimated_profit / revenue * 100.0)


def net_margin_percent(profit: float, net_revenue: float) -> float:
    """Actual/Projected margin: profit ÷ net revenue × 100."""
    if net_revenue <= 0:
        return 0.0
    return _money(float(profit) / float(net_revenue) * 100.0)


def margin_badge_class(margin_pct: float) -> str:
    if margin_pct >= 30:
        return "profit-margin-high"
    if margin_pct >= 15:
        return "profit-margin-mid"
    return "profit-margin-low"


def recalculate_and_save(booking_id: int) -> Optional[Dict[str, Any]]:
    row = db.get_booking(booking_id)
    if not row:
        return None
    booking = dict(row)
    booking["extra_charges"] = db.list_extra_charges(booking_id)
    metrics = calculate_booking_profit(booking)
    db.update_booking_profit_fields(
        booking_id,
        {
            "stripe_fee": metrics["stripe_fee"],
            "gst_amount": metrics["gst_amount"],
            "net_revenue": metrics["net_revenue"],
            "estimated_profit": metrics["estimated_profit"],
            "profit_margin_percent": metrics["profit_margin_percent"],
        },
    )
    return metrics


def parse_money_amount(raw: Any) -> Tuple[Optional[float], Optional[str]]:
    """Empty → $0. Reject negatives and non-numeric values."""
    if raw is None:
        return 0.0, None
    text = str(raw).strip()
    if not text:
        return 0.0, None
    try:
        value = _money(float(text))
    except (TypeError, ValueError):
        return None, "Enter a valid amount."
    if value < 0:
        return None, "Amount cannot be negative."
    return value, None


def job_cost_fields_in_form(form: Dict[str, Any]) -> bool:
    return any(key in form for key in JOB_COST_FIELDS)


def job_cost_form_errors(form: Dict[str, Any]) -> List[str]:
    if not job_cost_fields_in_form(form):
        return []
    errors = []
    for key, label in JOB_COST_FIELD_LABELS.items():
        if key not in form:
            continue
        _value, err = parse_money_amount(form.get(key))
        if err:
            errors.append("{0}: {1}".format(label, err))
    return errors


def parse_profit_cost_form(form: Dict[str, Any]) -> Dict[str, Any]:
    def _optional_float(key: str) -> Optional[float]:
        raw = (form.get(key) or "").strip()
        if not raw:
            return None
        try:
            return _money(float(raw))
        except ValueError:
            return None

    fields: Dict[str, Any] = {}
    for key in JOB_COST_FIELDS:
        if key not in form:
            continue
        value, err = parse_money_amount(form.get(key))
        fields[key] = 0.0 if err or value is None else value
    if "profit_crew_hours" in form:
        fields["profit_crew_hours"] = _optional_float("profit_crew_hours")
    if "profit_hourly_wage" in form:
        fields["profit_hourly_wage"] = _optional_float("profit_hourly_wage")
    return fields


def save_profit_cost_fields(booking_id: int, form: Dict[str, Any]) -> List[str]:
    errors = job_cost_form_errors(form)
    if errors:
        return errors
    fields = parse_profit_cost_form(form)
    if not fields:
        return []
    db.update_booking_profit_fields(booking_id, fields)
    return []


def is_included_in_monthly_summary(
    booking: Dict[str, Any],
    *,
    status_filter: str = "all",
    paid_only: bool = False,
    exclude_pending: bool = True,
) -> bool:
    status = job_status.display(booking)
    if exclude_pending and status == "Pending":
        return False
    if status == "Cancelled":
        return False
    if paid_only and (booking.get("payment_status") or "").strip() != invoice.PAYMENT_STATUS_PAID:
        return False
    if status_filter != "all" and status != status_filter:
        return False
    return True


def _month_bounds(month_key: str) -> tuple:
    year, month = month_key.split("-")
    first = date(int(year), int(month), 1)
    last_day = monthrange(first.year, first.month)[1]
    last = date(first.year, first.month, last_day)
    return first, last


def _aggregate_profit(bookings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum per-booking profit metrics. Each booking id counted once."""
    revenue = 0.0
    gst_amount = 0.0
    net_revenue = 0.0
    total_costs = 0.0
    estimated_profit = 0.0
    seen = set()
    jobs = 0
    for row in bookings:
        try:
            booking_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if booking_id in seen:
            continue
        seen.add(booking_id)
        jobs += 1
        metrics = calculate_booking_profit(row)
        revenue += metrics["revenue"]
        gst_amount += metrics["gst_amount"]
        net_revenue += metrics["net_revenue"]
        total_costs += metrics["total_costs"]
        estimated_profit += metrics["estimated_profit"]
    paid_revenue = _money(revenue)
    gst = _money(gst_amount)
    net = _money(net_revenue)
    costs = _money(total_costs)
    profit = _money(estimated_profit)
    return {
        "revenue": paid_revenue,
        "gst_amount": gst,
        "net_revenue": net,
        "total_costs": costs,
        "estimated_profit": profit,
        "margin_percent": net_margin_percent(profit, net),
        "job_count": jobs,
    }


def build_monthly_profit_summary(
    month_key: str,
    *,
    status_filter: str = "all",
    paid_only: bool = False,
) -> Dict[str, Any]:
    """
    Read-only Actual (Paid) vs Projected (Booked) monthly performance.

    Actual uses paid_at in Australia/Perth (same rule as Sales Dashboard).
    Projected uses move_date. Outstanding is all unpaid invoices, not the month.
    paid_only is ignored for Actual (always Paid) and kept for Projected if set.
    """
    start, end = _month_bounds(month_key)
    rows = sales_dashboard.load_unique_bookings()

    actual_bookings = sales_dashboard.paid_bookings_in_period(start, end, rows)
    actual = _aggregate_profit(actual_bookings)

    projected_bookings = []
    seen_projected = set()
    for row in rows:
        try:
            booking_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if booking_id in seen_projected:
            continue
        if not is_included_in_monthly_summary(
            row,
            status_filter=status_filter,
            paid_only=paid_only,
        ):
            continue
        move_on = sales_dashboard.booking_move_date(row)
        if move_on is None or not (start <= move_on <= end):
            continue
        seen_projected.add(booking_id)
        projected_bookings.append(row)
    projected = _aggregate_profit(projected_bookings)

    outstanding_amount, outstanding_count = sales_dashboard.outstanding_invoices(rows)

    actual_block = {
        "paid_revenue": actual["revenue"],
        "gst_collected": actual["gst_amount"],
        "net_revenue": actual["net_revenue"],
        "actual_costs": actual["total_costs"],
        "actual_profit": actual["estimated_profit"],
        "actual_margin": actual["margin_percent"],
        "paid_jobs": actual["job_count"],
    }
    projected_block = {
        "booked_revenue": projected["revenue"],
        "projected_gst": projected["gst_amount"],
        "projected_net_revenue": projected["net_revenue"],
        "projected_costs": projected["total_costs"],
        "projected_profit": projected["estimated_profit"],
        "projected_margin": projected["margin_percent"],
        "booked_jobs": projected["job_count"],
    }
    outstanding_block = {
        "amount": outstanding_amount,
        "count": outstanding_count,
    }
    return {
        "month": month_key,
        "month_start": start.isoformat(),
        "month_end": end.isoformat(),
        "actual": actual_block,
        "projected": projected_block,
        "outstanding": outstanding_block,
        "booking_count": projected_block["booked_jobs"],
        "revenue": projected_block["booked_revenue"],
        "gst_amount": projected_block["projected_gst"],
        "net_revenue": projected_block["projected_net_revenue"],
        "total_costs": projected_block["projected_costs"],
        "estimated_profit": projected_block["projected_profit"],
        "average_margin_percent": projected_block["projected_margin"],
        "status_filter": status_filter,
        "paid_only": paid_only,
        "exclude_pending": True,
    }


def profit_summary_for_booking(booking: Dict[str, Any]) -> Dict[str, Any]:
    if booking.get("id"):
        booking = dict(booking)
        booking["extra_charges"] = db.list_extra_charges(int(booking["id"]))
    metrics = calculate_booking_profit(booking)
    margin = metrics["profit_margin_percent"]
    return {
        **metrics,
        "margin_badge_class": margin_badge_class(margin),
    }
