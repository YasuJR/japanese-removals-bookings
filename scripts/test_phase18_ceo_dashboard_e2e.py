#!/usr/bin/env python3
"""Phase 18 E2E — CEO Dashboard (default home page)."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase18"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
import invoice
from ceo_dashboard_data import PHASE18_SECTIONS, build_ceo_dashboard


def _integration_status() -> dict:
    return {
        "gmail_automation_enabled": False,
        "gmail_scope_granted": False,
        "google_connected": False,
        "xero_ready": False,
        "stripe_ready": False,
        "sms_configured": False,
    }


def _create(
    label: str,
    *,
    status: str = "Confirmed",
    move_date: str = "",
    crew: str = "Yasu",
    truck: str = "Truck 1",
    phone: str = "0412000777",
    with_invoice: bool = True,
) -> int:
    move_day = move_date or date.today().isoformat()
    booking_id = db.create_booking(
        "{0} Customer".format(label),
        phone,
        "{0}@example.com".format(label.lower().replace(" ", "")),
        "10 CEO St, Perth WA",
        "20 Board Ave, Fremantle WA",
        move_day,
        2,
        "Phase 18 {0}".format(label),
        start_time="09:00",
        duration_hours="3",
        crew=crew,
        hourly_rate=110.0,
        gst_enabled=1,
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
        status=status,
    )
    db.update_booking_integration_fields(
        booking_id, {"truck_assigned": truck, "google_calendar_event_id": "evt_p18"}
    )
    if with_invoice:
        db.update_booking_invoice_fields(
            booking_id,
            {
                "invoice_number": "INV-P18-{0}".format(booking_id),
                "invoice_status": "AUTHORISED",
                "invoice_issue_date": date.today().isoformat(),
            },
        )
    if status != "Pending":
        db.update_booking_status(booking_id, status)
    return booking_id


def test_a_sections() -> dict:
    data = build_ceo_dashboard(date.today(), _integration_status())
    missing = [key for key in PHASE18_SECTIONS if key not in data]
    extra = [key for key in ("pipeline", "quick_stats") if key in data]
    failed = missing + ["Unexpected section: {0}".format(k) for k in extra]
    return {
        "name": "Test A — Phase 18 sections only",
        "pass": not failed,
        "details": failed or ["Seven Phase 18 sections present."],
    }


def test_b_today_counts() -> dict:
    _create("TodayB", status="Confirmed")
    _create("TodayRoute", status="On Route")
    data = build_ceo_dashboard(date.today(), _integration_status())
    section = data["today_section"]
    failed = []
    for key in ("jobs_today", "confirmed", "on_route", "completed", "not_started"):
        if key not in section:
            failed.append("Missing today key: {0}".format(key))
    if section["jobs_today"] < 2:
        failed.append("Expected at least 2 jobs today.")
    return {
        "name": "Test B — Today job counts",
        "pass": not failed,
        "details": failed or ["Today counts populated."],
    }


def test_c_tomorrow_warnings() -> dict:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    _create(
        "TomorrowC",
        move_date=tomorrow,
        crew="",
        truck="",
        phone="",
        with_invoice=False,
    )
    data = build_ceo_dashboard(date.today(), _integration_status())
    section = data["tomorrow_section"]
    failed = []
    for key in ("missing_crew", "missing_truck", "missing_phone", "missing_invoice"):
        if section.get(key, 0) < 1:
            failed.append("Tomorrow missing count for {0}".format(key))
    return {
        "name": "Test C — Tomorrow missing crew/truck/phone/invoice",
        "pass": not failed,
        "details": failed or ["Tomorrow warning counts correct."],
    }


def test_d_money() -> dict:
    _create("MoneyD", status="Paid")
    data = build_ceo_dashboard(date.today(), _integration_status())
    money = data["money"]
    failed = []
    for block in ("revenue", "profit", "margin_pct"):
        if block not in money:
            failed.append("Missing money block: {0}".format(block))
        elif set(money[block].keys()) != {"today", "week", "month"}:
            failed.append("Money block {0} missing period keys".format(block))
    if money["revenue"]["today"] <= 0:
        failed.append("Today revenue should be positive.")
    return {
        "name": "Test D — Money revenue, profit, margin",
        "pass": not failed,
        "details": failed or ["Money section valid."],
    }


def test_e_payments() -> dict:
    booking_id = _create("PayE", with_invoice=True)
    data = build_ceo_dashboard(date.today(), _integration_status())
    payments = data["payments"]
    failed = []
    for key in (
        "unpaid_count",
        "overdue_count",
        "paid_today_count",
        "total_outstanding",
    ):
        if key not in payments:
            failed.append("Missing payments key: {0}".format(key))
    if payments["unpaid_count"] < 1:
        failed.append("Expected unpaid invoices.")
    if payments["total_outstanding"] <= 0:
        failed.append("Total outstanding should be positive.")
    return {
        "name": "Test E — Payments including total outstanding",
        "pass": not failed,
        "details": failed or ["Payments section valid."],
        "booking_id": booking_id,
    }


def test_f_automation_health() -> dict:
    data = build_ceo_dashboard(date.today(), _integration_status())
    names = {s["name"] for s in data["automation_health"]}
    expected = {"Gmail", "Google Calendar", "Xero", "Stripe", "SMS"}
    failed = []
    if names != expected:
        failed.append("Automation services: {0}".format(sorted(names)))
    return {
        "name": "Test F — Automation health (5 integrations)",
        "pass": not failed,
        "details": failed or ["Five automation health cards present."],
    }


def test_g_alerts() -> dict:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    booking_id = _create(
        "AlertG", move_date=tomorrow, phone="", crew="Yasu", truck="Truck 1"
    )
    data = build_ceo_dashboard(date.today(), _integration_status())
    warnings = data["tomorrow_section"]["warnings"]
    failed = []
    if not any(
        w.get("code") == "missing_phone" and w.get("booking_id") == booking_id
        for w in warnings
    ):
        failed.append("Missing customer phone warning not raised for tomorrow job.")
    return {
        "name": "Test G — Alerts include missing phone",
        "pass": not failed,
        "details": failed or ["Tomorrow missing phone warning present."],
    }


def main() -> int:
    db.init_db()
    results = [
        test_a_sections(),
        test_b_today_counts(),
        test_c_tomorrow_warnings(),
        test_d_money(),
        test_e_payments(),
        test_f_automation_health(),
        test_g_alerts(),
    ]
    payload = {
        "phase": 18,
        "feature": "ceo_dashboard",
        "results": results,
        "all_pass": all(r["pass"] for r in results),
    }
    out_path = RESULTS_DIR / "phase18_results.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nPhase 18 — CEO Dashboard E2E\n")
    print("| Test | Result |")
    print("|------|--------|")
    for row in results:
        label = row["name"].split(" — ", 1)[0]
        status = "PASS" if row["pass"] else "FAIL"
        print("| {0} | **{1}** |".format(label, status))
        for detail in row["details"]:
            print("  - {0}".format(detail))
    print("\nOverall: {0}".format("PASS" if payload["all_pass"] else "FAIL"))
    print("Results saved to {0}".format(out_path))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
