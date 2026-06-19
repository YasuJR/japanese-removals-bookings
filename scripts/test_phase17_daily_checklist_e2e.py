#!/usr/bin/env python3
"""Phase 17 E2E — daily job checklist."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase17"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
import invoice
import job_status
import services
from daily_checklist_data import build_daily_checklist, build_checklist_sections, build_warnings


def _create_booking(
    label: str,
    *,
    status: str = "Confirmed",
    move_date: str = "",
    phone: str = "0412000555",
    crew: str = "Yasu",
    truck: str = "Truck 1",
    calendar_id: str = "cal_test",
    invoice_number: str = "INV-P17",
) -> int:
    move_day = move_date or date.today().isoformat()
    booking_id = db.create_booking(
        "{0} Customer".format(label),
        phone,
        "{0}@example.com".format(label.lower().replace(" ", "")),
        "10 Checklist St, Perth WA",
        "20 Route Ave, Fremantle WA",
        move_day,
        2,
        "Phase 17 {0}".format(label),
        start_time="09:00",
        finish_time="12:00",
        duration_hours="3",
        crew=crew,
        hourly_rate=110.0,
        gst_enabled=1,
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
        status=status,
    )
    db.update_booking_invoice_fields(
        booking_id,
        {
            "invoice_number": invoice_number,
            "invoice_status": "AUTHORISED" if invoice_number else "",
            "invoice_issue_date": date.today().isoformat() if invoice_number else "",
            "payment_status": invoice.PAYMENT_STATUS_UNPAID,
        },
    )
    db.update_booking_integration_fields(
        booking_id,
        {
            "google_calendar_event_id": calendar_id,
            "truck_assigned": truck,
        },
    )
    if status != "Pending":
        db.update_booking_status(booking_id, status)
    return booking_id


def test_today_includes_confirmed() -> dict:
    booking_id = _create_booking("TodayA", status="Confirmed")
    data = build_daily_checklist("today", date.today())
    ids = [j["booking_id"] for j in data["jobs"]]
    failed = []
    if booking_id not in ids:
        failed.append("Confirmed booking for today not listed.")
    return {
        "name": "Test A — Today filter includes Confirmed booking",
        "pass": not failed,
        "details": failed or ["Today checklist includes Confirmed job."],
        "booking_id": booking_id,
    }


def test_tomorrow_filter() -> dict:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    booking_id = _create_booking("TomorrowB", status="Paid", move_date=tomorrow)
    invoice.set_payment_status(booking_id, True)
    db.update_booking_status(booking_id, "Paid")
    today_data = build_daily_checklist("today", date.today())
    tomorrow_data = build_daily_checklist("tomorrow", date.today())
    failed = []
    if booking_id in [j["booking_id"] for j in today_data["jobs"]]:
        failed.append("Tomorrow job incorrectly shown in Today filter.")
    if booking_id not in [j["booking_id"] for j in tomorrow_data["jobs"]]:
        failed.append("Paid booking for tomorrow not in Tomorrow filter.")
    return {
        "name": "Test B — Tomorrow filter shows tomorrow jobs only",
        "pass": not failed,
        "details": failed or ["Tomorrow filter scoped correctly."],
        "booking_id": booking_id,
    }


def test_excludes_pending() -> dict:
    booking_id = _create_booking("PendingC", status="Pending")
    data = build_daily_checklist("today", date.today())
    failed = []
    if booking_id in [j["booking_id"] for j in data["jobs"]]:
        failed.append("Pending booking included in checklist.")
    return {
        "name": "Test C — Pending booking excluded",
        "pass": not failed,
        "details": failed or ["Pending booking excluded."],
        "booking_id": booking_id,
    }


def test_checklist_items() -> dict:
    booking_id = _create_booking("ChecklistD", crew="Yasu", truck="Truck 2")
    row = dict(db.get_booking(booking_id))
    sections = build_checklist_sections(row)
    failed = []
    before = {item["label"]: item["done"] for item in sections["before_job"]}
    if not before.get("Customer confirmed"):
        failed.append("Customer confirmed should be done.")
    if not before.get("Crew assigned"):
        failed.append("Crew assigned should be done.")
    if not before.get("Truck assigned"):
        failed.append("Truck assigned should be done.")
    row_no_crew = dict(row)
    row_no_crew["crew"] = ""
    row_no_crew["truck_assigned"] = ""
    before2 = {
        item["label"]: item["done"]
        for item in build_checklist_sections(row_no_crew)["before_job"]
    }
    if before2.get("Crew assigned"):
        failed.append("Crew assigned should be pending without crew.")
    if before2.get("Truck assigned"):
        failed.append("Truck assigned should be pending without truck.")
    return {
        "name": "Test D — Checklist items computed from booking data",
        "pass": not failed,
        "details": failed or ["Before-job checklist items reflect booking fields."],
        "booking_id": booking_id,
    }


def test_warnings() -> dict:
    booking_id = _create_booking(
        "WarningsE",
        phone="",
        crew="",
        truck="",
        calendar_id="",
        invoice_number="",
    )
    row = dict(db.get_booking(booking_id))
    warnings = {w["code"] for w in build_warnings(row)}
    expected = {"no_phone", "no_crew", "not_paid", "no_calendar", "no_invoice"}
    failed = []
    missing = expected - warnings
    if missing:
        failed.append("Missing warnings: {0}".format(", ".join(sorted(missing))))
    return {
        "name": "Test E — Warnings for missing phone, crew, payment, calendar, invoice",
        "pass": not failed,
        "details": failed or ["Expected warnings generated."],
        "booking_id": booking_id,
        "warnings": sorted(warnings),
    }


def test_mark_completed() -> dict:
    booking_id = _create_booking("CompleteF", status="Confirmed")
    with patch("integrations.review_automation.schedule_on_completed", return_value=None):
        ok, msg = services.mark_booking_completed(booking_id)
    row = dict(db.get_booking(booking_id))
    failed = []
    if not ok:
        failed.append("mark_booking_completed failed: {0}".format(msg))
    if job_status.display(row) != "Completed":
        failed.append("Status not updated to Completed.")
    sections = build_checklist_sections(row)
    after = {item["label"]: item["done"] for item in sections["after_job"]}
    if not after.get("Job marked Completed"):
        failed.append("After-job checklist not marked completed.")
    return {
        "name": "Test F — Mark completed updates status and checklist",
        "pass": not failed,
        "details": failed or ["Mark completed action works."],
        "booking_id": booking_id,
    }


def test_week_filter() -> dict:
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    in_week = (week_start + timedelta(days=2)).isoformat()
    booking_id = _create_booking("WeekG", status="Confirmed", move_date=in_week)
    data = build_daily_checklist("week", today)
    failed = []
    if booking_id not in [j["booking_id"] for j in data["jobs"]]:
        failed.append("Booking within current week not listed.")
    return {
        "name": "Test G — This week filter includes in-range jobs",
        "pass": not failed,
        "details": failed or ["Week filter includes job in current ISO week."],
        "booking_id": booking_id,
    }


def main() -> int:
    db.init_db()
    results = [
        test_today_includes_confirmed(),
        test_tomorrow_filter(),
        test_excludes_pending(),
        test_checklist_items(),
        test_warnings(),
        test_mark_completed(),
        test_week_filter(),
    ]
    payload = {
        "phase": 17,
        "feature": "daily_job_checklist",
        "results": results,
        "all_pass": all(r["pass"] for r in results),
    }
    out_path = RESULTS_DIR / "phase17_results.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nPhase 17 — Daily Job Checklist E2E\n")
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
