"""Executive dashboard v2 — business overview metrics."""

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import config
import database as db
import invoice
import job_status
from crew import CREW_OPTIONS, crew_from_storage
from crew_schedule_data import booking_duration_hours
from dashboard_data import week_range
from integrations import executive_config, review_config
from outstanding_invoices_data import (
    _is_paid,
    days_overdue,
    due_date_for,
    has_invoice,
)
from profit_data import (
    _is_countable,
    _money,
    _sum_metrics,
    calculate_booking_profit,
    month_range,
)

CREW_HOURS_PER_DAY = 8.0
CREW_WORK_DAYS_PER_WEEK = 5
COMPLETED_STATUSES = frozenset(
    {"Completed", "Invoiced", "Paid"}
)
WON_STATUSES = frozenset(
    {"Confirmed", "In Progress", "Completed", "Invoiced", "Paid"}
)


def _count_jobs(rows: List[Dict[str, Any]]) -> int:
    return len(rows)


def _booking_revenue(booking: Dict[str, Any]) -> float:
    return calculate_booking_profit(booking)["revenue"]


def _crew_capacity_hours(num_days: int) -> float:
    return len(CREW_OPTIONS) * CREW_HOURS_PER_DAY * num_days


def _crew_booked_hours(rows: List[Dict[str, Any]]) -> float:
    total = 0.0
    for row in rows:
        booking = dict(row)
        if job_status.display(booking) == "Cancelled":
            continue
        crew_names = crew_from_storage(booking.get("crew"))
        if not crew_names:
            continue
        hours = booking_duration_hours(booking)
        total += hours * len(crew_names)
    return round(total, 1)


def _utilization_pct(booked: float, capacity: float) -> float:
    if capacity <= 0:
        return 0.0
    return round(min(100.0, booked / capacity * 100.0), 1)


def _conversion_pct(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 1)


def _review_metrics() -> Dict[str, Any]:
    tracked = db.count_review_requests_by_status("reviewed")
    settings_count = review_config.get_google_review_count()
    settings_rating = review_config.get_google_average_rating()
    received = settings_count if settings_count is not None else tracked
    rating = settings_rating if settings_rating is not None else db.average_review_rating()
    return {
        "received": received,
        "tracked_received": tracked,
        "average_rating": rating,
        "rating_source": (
            "google"
            if settings_rating is not None
            else ("tracked" if rating is not None else None)
        ),
    }


def _revenue_target(month_revenue: float) -> Dict[str, Any]:
    target = executive_config.get_monthly_revenue_target()
    pct = round(month_revenue / target * 100.0, 1) if target > 0 else 0.0
    return {
        "target": target,
        "current": month_revenue,
        "pct": min(pct, 100.0) if pct > 100 else pct,
        "pct_raw": pct,
        "remaining": _money(max(target - month_revenue, 0.0)),
    }


def _top_customers_month(month_rows: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in month_rows:
        name = (row.get("customer_name") or "").strip() or "Unknown"
        entry = buckets.setdefault(
            name,
            {"customer_name": name, "revenue": 0.0, "jobs": 0},
        )
        entry["revenue"] += _booking_revenue(row)
        entry["jobs"] += 1
    ranked = sorted(
        buckets.values(),
        key=lambda item: (-item["revenue"], -item["jobs"], item["customer_name"]),
    )
    for item in ranked:
        item["revenue"] = _money(item["revenue"])
    return ranked[:limit]


def _daily_series(today: date, days: int = 30) -> List[Dict[str, Any]]:
    start = today - timedelta(days=days - 1)
    rows = [
        dict(r)
        for r in db.list_between_dates(start.isoformat(), today.isoformat())
        if _is_countable(dict(r))
    ]
    by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[row.get("move_date") or ""].append(row)

    series = []
    max_revenue = 0.0
    max_profit = 0.0
    cursor = start
    while cursor <= today:
        iso = cursor.isoformat()
        day_rows = by_date.get(iso, [])
        fin = _sum_metrics(day_rows)
        revenue = fin["revenue"]
        profit = fin["net_profit"]
        max_revenue = max(max_revenue, revenue)
        max_profit = max(max_profit, abs(profit))
        series.append(
            {
                "date": iso,
                "label": cursor.strftime("%d %b"),
                "revenue": revenue,
                "profit": profit,
            }
        )
        cursor += timedelta(days=1)

    for point in series:
        point["revenue_pct"] = (
            round(point["revenue"] / max_revenue * 100.0, 1)
            if max_revenue > 0
            else 0.0
        )
        profit_abs = abs(point["profit"])
        point["profit_pct"] = (
            round(profit_abs / max_profit * 100.0, 1) if max_profit > 0 else 0.0
        )
        point["profit_negative"] = point["profit"] < 0

    return series


def _review_conversion() -> Dict[str, Any]:
    stats = db.review_funnel_stats()
    sent = stats["sent"]
    clicked = stats["clicked"]
    received = stats["received"]
    return {
        "sent": sent,
        "clicked": clicked,
        "received": received,
        "conversion_pct": _conversion_pct(received, sent),
        "click_pct": _conversion_pct(clicked, sent),
    }


def _quote_conversion(month_start: date, month_end: date) -> Dict[str, Any]:
    rows = [
        dict(r)
        for r in db.list_created_between(month_start.isoformat(), month_end.isoformat())
    ]
    quotes_sent = len(rows)
    won = 0
    lost = 0
    for row in rows:
        status = job_status.display(row)
        if status == "Cancelled":
            lost += 1
        elif status in WON_STATUSES:
            won += 1
    decided = won + lost
    return {
        "quotes_sent": quotes_sent,
        "bookings_won": won,
        "bookings_lost": lost,
        "conversion_pct": _conversion_pct(won, decided),
    }


def _largest_unpaid_invoice(today: date) -> Optional[Dict[str, Any]]:
    candidates = []
    for row in db.list_all():
        booking = dict(row)
        if not has_invoice(booking) or _is_paid(booking):
            continue
        amount = _booking_revenue(booking)
        overdue_days = days_overdue(booking, today)
        candidates.append(
            {
                "customer_name": booking.get("customer_name") or "—",
                "amount": amount,
                "days_overdue": overdue_days,
                "booking_id": int(booking["id"]),
                "due_date": due_date_for(booking),
                "is_overdue": overdue_days is not None,
            }
        )
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            0 if item["is_overdue"] else 1,
            -(item["days_overdue"] or 0),
            -item["amount"],
        )
    )
    top = candidates[0]
    top["amount"] = _money(top["amount"])
    top["days_overdue_label"] = (
        str(top["days_overdue"]) if top["days_overdue"] is not None else "—"
    )
    return top


def _crew_leaderboard(month_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stats = {
        member: {
            "name": member,
            "jobs_completed": 0,
            "hours_worked": 0.0,
            "revenue_generated": 0.0,
        }
        for member in CREW_OPTIONS
    }
    for row in month_rows:
        booking = dict(row)
        status = job_status.display(booking)
        if status not in COMPLETED_STATUSES:
            continue
        crew_names = crew_from_storage(booking.get("crew"))
        if not crew_names:
            continue
        hours = booking_duration_hours(booking)
        revenue = _booking_revenue(booking)
        share = revenue / len(crew_names)
        for member in crew_names:
            if member not in stats:
                continue
            stats[member]["jobs_completed"] += 1
            stats[member]["hours_worked"] += hours
            stats[member]["revenue_generated"] += share

    leaderboard = []
    for member in CREW_OPTIONS:
        entry = stats[member]
        entry["hours_worked"] = round(entry["hours_worked"], 1)
        entry["revenue_generated"] = _money(entry["revenue_generated"])
        leaderboard.append(entry)
    leaderboard.sort(
        key=lambda item: (
            -item["revenue_generated"],
            -item["jobs_completed"],
            -item["hours_worked"],
        )
    )
    return leaderboard


def build_executive_dashboard(today: Optional[date] = None) -> Dict[str, Any]:
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

    today_fin = _sum_metrics(today_rows)
    week_fin = _sum_metrics(week_rows)
    month_fin = _sum_metrics(month_rows)

    from outstanding_invoices_data import build_outstanding_dashboard

    invoice_summary = build_outstanding_dashboard("unpaid", today)["summary"]
    overdue_count = invoice_summary["overdue_count"]

    week_booked = _crew_booked_hours(week_rows)
    week_capacity = _crew_capacity_hours(CREW_WORK_DAYS_PER_WEEK)
    today_booked = _crew_booked_hours(today_rows)
    today_capacity = _crew_capacity_hours(1)

    chart_series = _daily_series(today, 30)

    return {
        "today": today_iso,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "month_start": month_start.isoformat(),
        "month_end": month_end.isoformat(),
        "revenue": {
            "today": today_fin["revenue"],
            "week": week_fin["revenue"],
            "month": month_fin["revenue"],
        },
        "profit": {
            "today": today_fin["net_profit"],
            "week": week_fin["net_profit"],
            "month": month_fin["net_profit"],
        },
        "revenue_target": _revenue_target(month_fin["revenue"]),
        "outstanding": {
            "total": invoice_summary["outstanding_total"],
            "count": invoice_summary["outstanding_count"],
            "overdue_count": overdue_count,
            "has_overdue_warning": overdue_count > 0,
        },
        "jobs": {
            "today": _count_jobs(today_rows),
            "week": _count_jobs(week_rows),
        },
        "reviews": _review_metrics(),
        "review_conversion": _review_conversion(),
        "quote_conversion": _quote_conversion(month_start, month_end),
        "top_customers": _top_customers_month(month_rows),
        "largest_unpaid": _largest_unpaid_invoice(today),
        "crew_leaderboard": _crew_leaderboard(month_rows),
        "charts": {
            "revenue_30d": chart_series,
            "profit_30d": chart_series,
        },
        "crew": {
            "utilization_week_pct": _utilization_pct(week_booked, week_capacity),
            "utilization_today_pct": _utilization_pct(today_booked, today_capacity),
            "booked_hours_week": week_booked,
            "capacity_hours_week": week_capacity,
            "crew_count": len(CREW_OPTIONS),
        },
        "company_name": config.COMPANY_NAME,
        "settings": executive_config.settings_for_form(),
    }
