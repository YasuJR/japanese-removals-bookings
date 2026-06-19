#!/usr/bin/env python3
"""Phase 19 E2E — Crew & truck management."""

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase19"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
from ceo_dashboard_data import build_ceo_dashboard
from crew import crew_storage_value
from daily_checklist_data import build_warnings
from driver_run_sheet_data import build_driver_run_sheet
from resource_conflicts import (
    find_crew_conflict_warnings,
    find_truck_conflict_warnings,
    has_crew_conflict,
    has_truck_conflict,
)


def _move_date(days_ahead: int = 50) -> str:
    return (date.today() + timedelta(days=days_ahead)).isoformat()


def _unique_move_date(test_code: str) -> str:
    """Unique date per test invocation to avoid DB pollution across runs."""
    seed = hash("{0}-{1}".format(test_code, time.time_ns())) % 400
    return (date.today() + timedelta(days=300 + seed)).isoformat()


def _create(
    label: str,
    *,
    status: str = "Confirmed",
    move_date: str = "",
    start_time: str = "09:00",
    duration_hours: str = "3",
    crew: str = "Yasu",
    truck: str = "Truck 1",
) -> int:
    move_day = move_date or _move_date()
    booking_id = db.create_booking(
        "{0} Customer".format(label),
        "0412000999",
        "{0}@example.com".format(label.lower().replace(" ", "")),
        "10 Crew St, Perth WA",
        "20 Truck Ave, Fremantle WA",
        move_day,
        2,
        "Phase 19 {0}".format(label),
        start_time=start_time,
        duration_hours=duration_hours,
        crew=crew,
        status=status,
    )
    db.update_booking_integration_fields(booking_id, {"truck_assigned": truck})
    if status != "Pending":
        db.update_booking_status(booking_id, status)
    return booking_id


def test_a_tables() -> dict:
    db.init_db()
    crew = db.list_crew_members()
    trucks = db.list_trucks()
    failed = []
    if not crew:
        failed.append("Crew table empty after init.")
    else:
        required = {"name", "phone", "role", "active"}
        if not required.issubset(crew[0].keys()):
            failed.append("Crew row missing fields: {0}".format(required - crew[0].keys()))
    if not trucks:
        failed.append("Truck table empty after init.")
    else:
        required = {"name", "registration", "truck_type", "capacity", "active"}
        if not required.issubset(trucks[0].keys()):
            failed.append("Truck row missing fields.")
    return {
        "name": "Test A — Crew and truck tables",
        "pass": not failed,
        "details": failed or ["Tables seeded with required columns."],
    }


def test_b_crew_conflict_pending_ignored() -> dict:
    move_day = _unique_move_date("B")
    _create("CrewB1", move_date=move_day, crew="Yasu", status="Confirmed")
    pending_id = _create(
        "CrewB2",
        move_date=move_day,
        crew="Yasu",
        status="Pending",
    )
    pending = dict(db.get_booking(pending_id))
    failed = []
    if find_crew_conflict_warnings(pending, exclude_booking_id=pending_id):
        failed.append("Pending booking should not trigger crew conflict.")
    confirmed_id = _create(
        "CrewB3",
        move_date=move_day,
        crew="Yasu",
        status="Confirmed",
    )
    confirmed = dict(db.get_booking(confirmed_id))
    if not has_crew_conflict(confirmed, exclude_booking_id=confirmed_id):
        failed.append("Expected crew conflict between confirmed jobs.")
    return {
        "name": "Test B — Crew conflict; Pending ignored",
        "pass": not failed,
        "details": failed or ["Crew conflict rules correct."],
    }


def test_c_truck_conflict() -> dict:
    move_day = _unique_move_date("C")
    _create("TruckC1", move_date=move_day, truck="Truck 1", status="Confirmed")
    second_id = _create(
        "TruckC2",
        move_date=move_day,
        truck="Truck 1",
        status="Confirmed",
    )
    booking = dict(db.get_booking(second_id))
    failed = []
    if not has_truck_conflict(booking, exclude_booking_id=second_id):
        failed.append("Expected truck conflict.")
    warnings = find_truck_conflict_warnings(booking, exclude_booking_id=second_id)
    if not warnings:
        failed.append("Expected truck conflict warning message.")
    return {
        "name": "Test C — Truck double booking",
        "pass": not failed,
        "details": failed or ["Truck conflict detected."],
    }


def test_d_cancelled_ignored() -> dict:
    move_day = _unique_move_date("D")
    _create("CancelD1", move_date=move_day, crew="Tom", truck="Truck 2", status="Cancelled")
    booking_id = _create(
        "CancelD2",
        move_date=move_day,
        crew="Tom",
        truck="Truck 2",
        status="Confirmed",
    )
    booking = dict(db.get_booking(booking_id))
    failed = []
    if has_crew_conflict(booking, exclude_booking_id=booking_id):
        failed.append("Cancelled booking should not block crew.")
    if has_truck_conflict(booking, exclude_booking_id=booking_id):
        failed.append("Cancelled booking should not block truck.")
    return {
        "name": "Test D — Cancelled bookings ignored",
        "pass": not failed,
        "details": failed or ["Cancelled jobs ignored for conflicts."],
    }


def test_e_driver_run_sheet_truck_filter() -> dict:
    move_day = _unique_move_date("E")
    _create(
        "RunE1",
        move_date=move_day,
        crew="Ken",
        truck="Truck 1",
        start_time="08:00",
    )
    _create(
        "RunE2",
        move_date=move_day,
        crew="Ken",
        truck="Truck 2",
        start_time="13:00",
    )
    all_jobs = build_driver_run_sheet("Ken", move_day, date.today())
    truck1 = build_driver_run_sheet("Ken", move_day, date.today(), truck_name="Truck 1")
    failed = []
    if all_jobs["job_count"] != 2:
        failed.append("Expected 2 jobs for Ken without truck filter.")
    if truck1["job_count"] != 1:
        failed.append("Expected 1 job when filtered by Truck 1.")
    return {
        "name": "Test E — Driver run sheet truck filter",
        "pass": not failed,
        "details": failed or ["Truck filter works on run sheet."],
    }


def test_f_dashboard_warnings() -> dict:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    _create(
        "DashF",
        move_date=tomorrow,
        crew="",
        truck="",
        status="Confirmed",
    )
    data = build_ceo_dashboard(date.today(), {})
    section = data["tomorrow_section"]
    failed = []
    if section.get("missing_crew", 0) < 1:
        failed.append("Missing crew warning expected.")
    if section.get("missing_truck", 0) < 1:
        failed.append("Missing truck warning expected.")
    return {
        "name": "Test F — Dashboard missing crew/truck warnings",
        "pass": not failed,
        "details": failed or ["CEO dashboard warnings present."],
    }


def test_g_checklist_conflicts() -> dict:
    move_day = _unique_move_date("G")
    _create("CheckG1", move_date=move_day, crew="Yasu", status="Confirmed")
    booking_id = _create(
        "CheckG2",
        move_date=move_day,
        crew="Yasu",
        status="Confirmed",
    )
    booking = dict(db.get_booking(booking_id))
    warnings = build_warnings(booking)
    codes = {w["code"] for w in warnings}
    failed = []
    if "crew_conflict" not in codes:
        failed.append("Checklist should show crew conflict warning.")
    return {
        "name": "Test G — Checklist crew conflict warning",
        "pass": not failed,
        "details": failed or ["Checklist conflict warning present."],
    }


def main() -> int:
    db.init_db()
    results = [
        test_a_tables(),
        test_b_crew_conflict_pending_ignored(),
        test_c_truck_conflict(),
        test_d_cancelled_ignored(),
        test_e_driver_run_sheet_truck_filter(),
        test_f_dashboard_warnings(),
        test_g_checklist_conflicts(),
    ]
    payload = {
        "phase": 19,
        "feature": "crew_truck_management",
        "results": results,
        "all_pass": all(r["pass"] for r in results),
    }
    out_path = RESULTS_DIR / "phase19_results.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nPhase 19 — Crew & Truck Management E2E\n")
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
