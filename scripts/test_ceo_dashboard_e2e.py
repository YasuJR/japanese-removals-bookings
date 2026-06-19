#!/usr/bin/env python3
"""CEO Dashboard E2E tests."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "ceo_dashboard"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
import invoice
import job_status
from ceo_dashboard_data import build_ceo_dashboard


def _integration_status() -> dict:
    return {
        "gmail_automation_enabled": False,
        "gmail_scope_granted": False,
        "google_connected": False,
        "google_configured": False,
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
    phone: str = "0412000666",
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
        "CEO test {0}".format(label),
        start_time="09:00",
        finish_time="12:00",
        duration_hours="3",
        crew=crew,
        hourly_rate=110.0,
        gst_enabled=1,
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
        status=status,
    )
    db.update_booking_integration_fields(
        booking_id,
        {"truck_assigned": truck, "google_calendar_event_id": "evt_ceo_test"},
    )
    db.update_booking_invoice_fields(
        booking_id,
        {
            "invoice_number": "INV-CEO-{0}".format(booking_id),
            "invoice_status": "AUTHORISED",
            "invoice_issue_date": date.today().isoformat(),
        },
    )
    if status != "Pending":
        db.update_booking_status(booking_id, status)
    return booking_id


def test_sections_present() -> dict:
    data = build_ceo_dashboard(date.today(), _integration_status())
    required = (
        "today_section",
        "tomorrow_section",
        "money",
        "payments",
        "pipeline",
        "automation_health",
        "alerts",
        "quick_stats",
        "quick_actions",
    )
    failed = [key for key in required if key not in data]
    return {
        "name": "Test A — Dashboard exposes all nine sections",
        "pass": not failed,
        "details": failed or ["All required sections present."],
    }


def test_today_jobs() -> dict:
    booking_id = _create("TodayJob", status="Confirmed")
    data = build_ceo_dashboard(date.today(), _integration_status())
    ids = [j["booking_id"] for j in data["today_section"]["jobs"]]
    failed = []
    if booking_id not in ids:
        failed.append("Today's Confirmed job missing from today section.")
    if data["today_section"]["total"] < 1:
        failed.append("Today job count should be at least 1.")
    return {
        "name": "Test B — Today section lists today's jobs with counts",
        "pass": not failed,
        "details": failed or ["Today section populated."],
        "booking_id": booking_id,
    }


def test_tomorrow_warnings() -> dict:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    booking_id = _create(
        "TomorrowWarn",
        move_date=tomorrow,
        crew="",
        truck="",
    )
    db.update_booking_invoice_fields(booking_id, {"invoice_number": "", "invoice_status": ""})
    data = build_ceo_dashboard(date.today(), _integration_status())
    codes = {w["code"] for w in data["tomorrow_section"]["warnings"]}
    failed = []
    for code in ("missing_crew", "missing_truck", "missing_invoice"):
        if code not in codes:
            failed.append("Missing tomorrow warning: {0}".format(code))
    return {
        "name": "Test C — Tomorrow warnings for crew, truck, invoice",
        "pass": not failed,
        "details": failed or ["Tomorrow warnings generated."],
        "booking_id": booking_id,
    }


def test_money_metrics() -> dict:
    _create("MoneyJob", status="Paid")
    data = build_ceo_dashboard(date.today(), _integration_status())
    block = data["money"]["today"]
    failed = []
    for key in ("revenue", "gst", "costs", "profit", "margin_pct"):
        if key not in block:
            failed.append("Missing money key: {0}".format(key))
    if block.get("revenue", 0) <= 0:
        failed.append("Today revenue should be positive with a paid job.")
    return {
        "name": "Test D — Money section includes revenue, GST, costs, profit, margin",
        "pass": not failed,
        "details": failed or ["Money metrics present."],
    }


def test_pipeline_counts() -> dict:
    _create("PipePending", status="Pending")
    _create("PipeConfirmed", status="Confirmed")
    data = build_ceo_dashboard(date.today(), _integration_status())
    stages = {s["status"]: s for s in data["pipeline"]["stages"]}
    failed = []
    if stages.get("Pending", {}).get("count", 0) < 1:
        failed.append("Pending pipeline count missing.")
    if stages.get("Confirmed", {}).get("count", 0) < 1:
        failed.append("Confirmed pipeline count missing.")
    if "value" not in stages.get("Confirmed", {}):
        failed.append("Pipeline stage missing value.")
    return {
        "name": "Test E — Bookings pipeline shows counts and values",
        "pass": not failed,
        "details": failed or ["Pipeline stages populated."],
    }


def test_automation_health() -> dict:
    data = build_ceo_dashboard(date.today(), _integration_status())
    failed = []
    if len(data["automation_health"]) != 5:
        failed.append("Expected five automation health services.")
    for service in data["automation_health"]:
        if "status_class" not in service or "status_label" not in service:
            failed.append("Automation health entry missing status fields.")
    return {
        "name": "Test F — Automation health lists five integrations",
        "pass": not failed,
        "details": failed or ["Automation health section valid."],
    }


def test_job_card_actions() -> dict:
    booking_id = _create("ActionsJob", status="Confirmed")
    data = build_ceo_dashboard(date.today(), _integration_status())
    job = next(
        (j for j in data["today_section"]["jobs"] if j["booking_id"] == booking_id),
        None,
    )
    failed = []
    if not job:
        failed.append("Job card not found.")
    elif not job.get("has_phone"):
        failed.append("Job card should expose phone actions.")
    return {
        "name": "Test G — Today job cards support quick actions",
        "pass": not failed,
        "details": failed or ["Job card includes phone action flags."],
        "booking_id": booking_id,
    }


def main() -> int:
    db.init_db()
    results = [
        test_sections_present(),
        test_today_jobs(),
        test_tomorrow_warnings(),
        test_money_metrics(),
        test_pipeline_counts(),
        test_automation_health(),
        test_job_card_actions(),
    ]
    payload = {
        "feature": "ceo_dashboard",
        "results": results,
        "all_pass": all(r["pass"] for r in results),
    }
    out_path = RESULTS_DIR / "ceo_dashboard_results.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nCEO Dashboard E2E\n")
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
