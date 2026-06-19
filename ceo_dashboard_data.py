"""Phase 18 — CEO dashboard (default home page)."""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import automation
import config
import database as db
import double_booking
import invoice
import job_status
from booking_helpers import tel_href
from booking_times import display_finish_time, display_start_time
from crew import crew_from_storage, display_crew
from resource_conflicts import has_crew_conflict, has_truck_conflict
from dashboard_data import week_range
from integrations import payment_reminder_automation
from outstanding_invoices_data import (
    _is_overdue,
    _is_paid,
    days_overdue,
    has_invoice,
    invoice_number_display,
)
from profit_data import _is_countable, _money, _sum_metrics

NOT_STARTED_STATUSES = frozenset({"Pending", "Quote", "Confirmed", "Paid"})
AUTOMATION_ERROR_STATUSES = frozenset(
    {automation.STATUS_ERROR, automation.STATUS_FAILED}
)

PHASE18_SECTIONS = (
    "today_section",
    "tomorrow_section",
    "money",
    "payments",
    "automation_health",
    "alerts",
    "quick_actions",
)


def _rows_for_period(start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
    rows = [
        dict(r)
        for r in db.list_between_dates(start_iso, end_iso)
        if _is_countable(dict(r))
    ]
    for row in rows:
        if row.get("id"):
            row["extra_charges"] = db.list_extra_charges(int(row["id"]))
    return rows


def _job_card(booking: Dict[str, Any]) -> Dict[str, Any]:
    phone = (booking.get("phone") or "").strip()
    return {
        "booking_id": int(booking["id"]),
        "customer_name": booking.get("customer_name") or "",
        "phone": phone,
        "status": job_status.display(booking),
        "start_display": display_start_time(booking),
        "finish_display": display_finish_time(booking),
        "pickup_address": booking.get("pickup_address") or "",
        "delivery_address": booking.get("delivery_address") or "",
        "crew_display": display_crew(booking),
        "truck_assigned": (booking.get("truck_assigned") or "").strip(),
        "has_phone": bool(phone and tel_href(phone)),
    }


def _today_section(today_iso: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    active = [r for r in rows if job_status.display(r) != "Cancelled"]
    return {
        "date": today_iso,
        "jobs_today": len(active),
        "confirmed": sum(1 for r in active if job_status.display(r) == "Confirmed"),
        "on_route": sum(1 for r in active if job_status.display(r) == "On Route"),
        "completed": sum(
            1 for r in active if job_status.display(r) in ("Completed", "Invoiced")
        ),
        "not_started": sum(
            1 for r in active if job_status.display(r) in NOT_STARTED_STATUSES
        ),
        "jobs": [_job_card(r) for r in active],
    }


def _tomorrow_section(tomorrow_iso: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    active = [r for r in rows if job_status.display(r) != "Cancelled"]
    warnings: List[Dict[str, Any]] = []
    missing_crew = 0
    missing_truck = 0
    missing_phone = 0
    missing_invoice = 0
    crew_conflict = 0
    truck_conflict = 0

    for row in active:
        bid = int(row["id"])
        name = row.get("customer_name") or "Booking #{0}".format(bid)
        phone = (row.get("phone") or "").strip()
        if not crew_from_storage(row.get("crew")):
            missing_crew += 1
            warnings.append(
                {
                    "code": "missing_crew",
                    "label": "Missing crew",
                    "booking_id": bid,
                    "customer_name": name,
                }
            )
        if not (row.get("truck_assigned") or "").strip():
            missing_truck += 1
            warnings.append(
                {
                    "code": "missing_truck",
                    "label": "Missing truck",
                    "booking_id": bid,
                    "customer_name": name,
                }
            )
        if not phone or not tel_href(phone):
            missing_phone += 1
            warnings.append(
                {
                    "code": "missing_phone",
                    "label": "Missing phone",
                    "booking_id": bid,
                    "customer_name": name,
                }
            )
        if not payment_reminder_automation.has_authorised_invoice(row):
            missing_invoice += 1
            warnings.append(
                {
                    "code": "missing_invoice",
                    "label": "Missing invoice",
                    "booking_id": bid,
                    "customer_name": name,
                }
            )
        if has_crew_conflict(row, exclude_booking_id=bid):
            crew_conflict += 1
            warnings.append(
                {
                    "code": "crew_conflict",
                    "label": "Crew conflict",
                    "booking_id": bid,
                    "customer_name": name,
                }
            )
        if has_truck_conflict(row, exclude_booking_id=bid):
            truck_conflict += 1
            warnings.append(
                {
                    "code": "truck_conflict",
                    "label": "Truck conflict",
                    "booking_id": bid,
                    "customer_name": name,
                }
            )

    return {
        "date": tomorrow_iso,
        "jobs_tomorrow": len(active),
        "missing_crew": missing_crew,
        "missing_truck": missing_truck,
        "missing_phone": missing_phone,
        "missing_invoice": missing_invoice,
        "crew_conflict": crew_conflict,
        "truck_conflict": truck_conflict,
        "warnings": warnings,
        "jobs": [_job_card(r) for r in active],
    }


def _money_section(
    today_rows: List[Dict[str, Any]],
    week_rows: List[Dict[str, Any]],
    month_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    today = _sum_metrics(today_rows)
    week = _sum_metrics(week_rows)
    month = _sum_metrics(month_rows)
    return {
        "revenue": {
            "today": today["revenue"],
            "week": week["revenue"],
            "month": month["revenue"],
        },
        "profit": {
            "today": today["estimated_profit"],
            "week": week["estimated_profit"],
            "month": month["estimated_profit"],
        },
        "margin_pct": {
            "today": today["margin_pct"],
            "week": week["margin_pct"],
            "month": month["margin_pct"],
        },
    }


def _payments_section(today: date) -> Dict[str, Any]:
    today_iso = today.isoformat()
    invoiced = [dict(r) for r in db.list_all() if has_invoice(dict(r))]
    unpaid = [b for b in invoiced if not _is_paid(b)]
    overdue = [b for b in unpaid if _is_overdue(b, today)]
    paid_today = [
        b
        for b in invoiced
        if _is_paid(b) and (b.get("paid_at") or "").strip()[:10] == today_iso
    ]
    total_outstanding = 0.0
    for booking in unpaid:
        booking["extra_charges"] = db.list_extra_charges(int(booking["id"]))
        total_outstanding += invoice.calculate_invoice_totals(booking)["total"]

    def _payment_row(booking: Dict[str, Any]) -> Dict[str, Any]:
        booking["extra_charges"] = db.list_extra_charges(int(booking["id"]))
        totals = invoice.calculate_invoice_totals(booking)
        overdue_days = days_overdue(booking, today)
        return {
            "booking_id": int(booking["id"]),
            "customer_name": booking.get("customer_name") or "",
            "amount": totals["total"],
            "days_overdue": overdue_days,
            "days_overdue_label": (
                str(overdue_days) if overdue_days is not None else "—"
            ),
        }

    return {
        "unpaid_count": len(unpaid),
        "overdue_count": len(overdue),
        "paid_today_count": len(paid_today),
        "total_outstanding": _money(total_outstanding),
        "unpaid": [_payment_row(b) for b in unpaid[:8]],
        "overdue": [_payment_row(b) for b in overdue[:8]],
        "paid_today": [_payment_row(b) for b in paid_today[:8]],
    }


def _recent_errors(limit: int = 40) -> List[Dict[str, Any]]:
    return [
        entry
        for entry in db.list_automation_logs(limit=limit)
        if (entry.get("status") or "").strip().lower() in AUTOMATION_ERROR_STATUSES
    ]


def _automation_health(integration_status: Dict[str, Any]) -> List[Dict[str, Any]]:
    errors = _recent_errors(30)
    error_types = {e.get("automation_type") for e in errors}

    def _service(name: str, working: bool) -> Dict[str, Any]:
        return {
            "name": name,
            "working": working,
            "status_class": "ceo-health-ok" if working else "ceo-health-error",
            "status_label": "Working" if working else "Error",
        }

    gmail_ok = (
        not config.GMAIL_INBOX_ENABLED
        or (
            integration_status.get("gmail_automation_enabled")
            and integration_status.get("gmail_scope_granted")
            and not (error_types & set(automation.GMAIL_AUTOMATION_TYPES))
        )
    )
    calendar_ok = (
        not config.GOOGLE_CALENDAR_ENABLED
        or (
            integration_status.get("google_connected")
            and not (
                error_types & {automation.AUTOMATION_CALENDAR_EVENT_SYNCED}
            )
        )
    )
    xero_ok = (
        not config.XERO_ENABLED
        or (
            integration_status.get("xero_ready")
            and not (error_types & set(automation.XERO_AUTOMATION_TYPES))
        )
    )
    stripe_ok = integration_status.get("stripe_ready") and not (
        error_types & set(automation.STRIPE_AUTOMATION_TYPES)
    )
    sms_ok = (
        not integration_status.get("sms_configured")
        or (
            integration_status.get("sms_configured")
            and not (error_types & set(automation.SMS_AUTOMATION_TYPES))
        )
    )

    return [
        _service("Gmail", gmail_ok),
        _service("Google Calendar", calendar_ok),
        _service("Xero", xero_ok),
        _service("Stripe", stripe_ok),
        _service("SMS", sms_ok),
    ]


def _alerts_section(
    today_rows: List[Dict[str, Any]],
    tomorrow_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    seen = set()

    def _add(code: str, label: str, booking_id: int = 0) -> None:
        key = (code, booking_id, label)
        if key in seen:
            return
        seen.add(key)
        alerts.append(
            {"code": code, "label": label, "booking_id": booking_id or None}
        )

    for row in today_rows + tomorrow_rows:
        booking = dict(row)
        bid = int(booking["id"])
        if double_booking.badge_for_booking(booking) == "conflict":
            _add("double_booking", "Double booking", bid)
        phone = (booking.get("phone") or "").strip()
        if not phone or not tel_href(phone):
            _add("missing_phone", "Missing customer phone", bid)
        if not (booking.get("pickup_address") or "").strip():
            _add("missing_address", "Missing address", bid)
        if not (booking.get("delivery_address") or "").strip():
            _add("missing_address", "Missing address", bid)
        if has_crew_conflict(booking, exclude_booking_id=bid):
            _add("crew_conflict", "Crew conflict", bid)
        if has_truck_conflict(booking, exclude_booking_id=bid):
            _add("truck_conflict", "Truck conflict", bid)

    for entry in db.list_sms_delivery_logs(15):
        if (entry.get("status") or "").strip().lower() in ("failed", "undelivered"):
            bid = int(entry["booking_id"]) if entry.get("booking_id") else 0
            _add("failed_sms", "Failed SMS", bid)

    for row in db.list_all():
        booking = dict(row)
        if (booking.get("xero_invoice_automation_error") or "").strip():
            _add("failed_invoice", "Failed invoice", int(booking["id"]))

    for entry in _recent_errors(20):
        if (entry.get("automation_type") or "") == automation.AUTOMATION_CALENDAR_EVENT_SYNCED:
            bid = int(entry["booking_id"]) if entry.get("booking_id") else 0
            _add("failed_calendar", "Failed calendar sync", bid)

    return alerts[:20]


def build_ceo_dashboard(
    today: Optional[date] = None,
    integration_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if today is None:
        today = date.today()
    if integration_status is None:
        integration_status = {}

    today_iso = today.isoformat()
    tomorrow_iso = (today + timedelta(days=1)).isoformat()
    week_start, week_end = week_range(today)
    month_start, month_end = month_range(today)

    today_rows = _rows_for_period(today_iso, today_iso)
    tomorrow_rows = _rows_for_period(tomorrow_iso, tomorrow_iso)
    week_rows = _rows_for_period(week_start.isoformat(), week_end.isoformat())
    month_rows = _rows_for_period(month_start.isoformat(), month_end.isoformat())

    return {
        "today": today_iso,
        "company_name": config.COMPANY_NAME,
        "today_section": _today_section(today_iso, today_rows),
        "tomorrow_section": _tomorrow_section(tomorrow_iso, tomorrow_rows),
        "money": _money_section(today_rows, week_rows, month_rows),
        "payments": _payments_section(today),
        "automation_health": _automation_health(integration_status),
        "alerts": _alerts_section(today_rows, tomorrow_rows),
        "quick_actions": {},
    }


def month_range(today: date) -> tuple:
    from calendar import monthrange

    first = today.replace(day=1)
    last_day = monthrange(today.year, today.month)[1]
    last = today.replace(day=last_day)
    return first, last
