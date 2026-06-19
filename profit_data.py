"""Job profit calculations and dashboard aggregates."""

from calendar import monthrange
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import booking_profit
import database as db
import job_status
from dashboard_data import week_range

PROFIT_CSV_HEADERS = [
    "id",
    "customer_name",
    "move_date",
    "status",
    "revenue",
    "gst_amount",
    "net_revenue",
    "staff_cost",
    "fuel_cost",
    "truck_cost",
    "stripe_fee",
    "other_costs",
    "estimated_profit",
    "profit_margin_percent",
]


def _money(value: float) -> float:
    return round(float(value), 2)


def calculate_booking_profit(booking: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 15 estimated profit for one booking."""
    return booking_profit.calculate_booking_profit(booking)


def _profit_row(booking: Dict[str, Any]) -> Dict[str, Any]:
    metrics = calculate_booking_profit(booking)
    return {
        "id": int(booking["id"]),
        "customer_name": booking.get("customer_name") or "",
        "move_date": booking.get("move_date") or "",
        "status": job_status.display(booking),
        **metrics,
        "net_profit": metrics["estimated_profit"],
        "gross_profit": metrics["net_revenue"]
        - metrics["staff_cost"]
        - metrics["fuel_cost"]
        - metrics["truck_cost"],
        "margin_pct": metrics["profit_margin_percent"],
    }


def _is_countable(booking: Dict[str, Any]) -> bool:
    return job_status.display(booking) not in ("Cancelled", "Pending")


def _sum_metrics(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    revenue = 0.0
    gst_amount = 0.0
    net_revenue = 0.0
    total_costs = 0.0
    estimated_profit = 0.0
    staff_cost = 0.0
    fuel_cost = 0.0
    truck_cost = 0.0
    for row in rows:
        metrics = calculate_booking_profit(row)
        revenue += metrics["revenue"]
        gst_amount += metrics["gst_amount"]
        net_revenue += metrics["net_revenue"]
        total_costs += metrics["total_costs"]
        estimated_profit += metrics["estimated_profit"]
        staff_cost += metrics["staff_cost"]
        fuel_cost += metrics["fuel_cost"]
        truck_cost += metrics["truck_cost"]
    rev = _money(revenue)
    profit = _money(estimated_profit)
    net_rev = _money(net_revenue)
    return {
        "revenue": rev,
        "gst_amount": _money(gst_amount),
        "net_revenue": net_rev,
        "total_costs": _money(total_costs),
        "estimated_profit": profit,
        "gross_profit": _money(net_rev - staff_cost - fuel_cost - truck_cost),
        "net_profit": profit,
        "margin_pct": booking_profit.profit_margin_percent(rev, profit),
    }


def month_range(today: date) -> Tuple[date, date]:
    first = today.replace(day=1)
    last_day = monthrange(today.year, today.month)[1]
    last = today.replace(day=last_day)
    return first, last


def build_profit_dashboard(today: Optional[date] = None) -> Dict[str, Any]:
    if today is None:
        today = date.today()

    today_iso = today.isoformat()
    week_start, week_end = week_range(today)
    month_start, month_end = month_range(today)

    today_rows = [
        dict(r) for r in db.list_by_date(today_iso) if _is_countable(dict(r))
    ]
    week_rows = [
        dict(r)
        for r in db.list_between_dates(week_start.isoformat(), week_end.isoformat())
        if _is_countable(dict(r))
    ]
    month_rows = [
        dict(r)
        for r in db.list_between_dates(month_start.isoformat(), month_end.isoformat())
        if _is_countable(dict(r))
    ]

    all_rows = [dict(r) for r in db.list_all() if _is_countable(dict(r))]
    for row in all_rows + today_rows + week_rows + month_rows:
        row["extra_charges"] = db.list_extra_charges(int(row["id"]))
    profit_rows = [_profit_row(b) for b in all_rows]
    profit_rows.sort(key=lambda r: r["estimated_profit"], reverse=True)

    top_profitable = profit_rows[:10]
    lowest_profit = sorted(profit_rows, key=lambda r: r["estimated_profit"])[:10]

    today_totals = _sum_metrics(today_rows)
    week_totals = _sum_metrics(week_rows)
    month_totals = _sum_metrics(month_rows)

    return {
        "today": today_iso,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "month_start": month_start.isoformat(),
        "month_end": month_end.isoformat(),
        "periods": [
            {
                "label": "Today",
                "range": today_iso,
                **today_totals,
            },
            {
                "label": "This week",
                "range": "{0} – {1}".format(
                    week_start.isoformat(), week_end.isoformat()
                ),
                **week_totals,
            },
            {
                "label": "This month",
                "range": "{0} – {1}".format(
                    month_start.isoformat(), month_end.isoformat()
                ),
                **month_totals,
            },
        ],
        "top_profitable": top_profitable,
        "lowest_profit": lowest_profit,
        "all_jobs": profit_rows,
        "job_count": len(profit_rows),
        "formula_hint": (
            "Estimated profit = Revenue − GST − Stripe fee − Staff − Fuel − "
            "Truck − Other costs. GST = total ÷ 11 when GST-inclusive. "
            "Pending bookings excluded."
        ),
    }


def profit_csv_rows() -> List[List[Any]]:
    rows = []
    for booking in db.list_all():
        booking = dict(booking)
        if not _is_countable(booking):
            continue
        booking["extra_charges"] = db.list_extra_charges(int(booking["id"]))
        profit = _profit_row(booking)
        rows.append([profit.get(h, "") for h in PROFIT_CSV_HEADERS])
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows
