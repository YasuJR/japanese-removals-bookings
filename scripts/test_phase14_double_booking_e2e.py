#!/usr/bin/env python3
"""Phase 14 E2E — double booking prevention tests A–E."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase14"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import automation
import database as db
import double_booking
import services


def _move_date(days_ahead: int = 45) -> str:
    return (date.today() + timedelta(days=days_ahead)).isoformat()


def _create(
    label: str,
    *,
    status: str,
    start_time: str = "09:00",
    duration_hours: str = "3",
    move_date: str = "",
) -> int:
    move_day = move_date or _move_date()
    return db.create_booking(
        "{0} Customer".format(label),
        "0412000111",
        "{0}@example.com".format(label.lower().replace(" ", "")),
        "10 Test St, Perth WA",
        "20 Demo Ave, Fremantle WA",
        move_day,
        2,
        "Phase 14 {0}".format(label),
        start_time=start_time,
        finish_time="",
        duration_hours=duration_hours,
        status=status,
    )


def _booking_row(booking_id: int) -> dict:
    return dict(db.get_booking(booking_id))


def _payload_from_row(row: dict, *, status: str = None) -> dict:
    return {
        "move_date": row["move_date"],
        "start_time": row["start_time"] or "09:00",
        "finish_time": row.get("finish_time") or "",
        "duration_hours": row.get("duration_hours") or "3",
        "status": status or row.get("status") or "Pending",
        "customer_name": row.get("customer_name") or "",
    }


def _logs(booking_id: int, automation_type: str) -> list:
    return [
        e
        for e in db.list_automation_logs(limit=50)
        if e.get("booking_id") == booking_id
        and e.get("automation_type") == automation_type
    ]


def test_a(move_date: str) -> dict:
    confirmed_id = _create("ConfirmedA", status="Confirmed", move_date=move_date)
    pending_id = _create("PendingA", status="Pending", move_date=move_date)

    pending = _booking_row(pending_id)
    badge = double_booking.badge_for_booking(pending)
    conflicts = double_booking.find_conflicts(
        double_booking.booking_payload_from_form(
            _payload_from_row(pending), pending_id
        ),
        exclude_booking_id=pending_id,
    )
    save_errors, _, _ = double_booking.validate_save(
        _payload_from_row(pending), booking_id=pending_id
    )
    calendar_msgs = services.after_booking_created(pending_id)
    pending_after = _booking_row(pending_id)

    passed = (
        badge is None
        and bool(conflicts)
        and not save_errors
        and not pending_after.get("google_calendar_event_id")
        and not calendar_msgs
    )
    return {
        "name": "Test A — Pending overlaps Confirmed",
        "passed": passed,
        "details": {
            "confirmed_id": confirmed_id,
            "pending_id": pending_id,
            "badge": badge,
            "conflict_count": len(conflicts),
            "save_errors": save_errors,
            "calendar_event_id": pending_after.get("google_calendar_event_id"),
            "calendar_messages": calendar_msgs,
        },
    }


def test_b(move_date: str) -> dict:
    first_id = _create("ConfirmedB1", status="Confirmed", move_date=move_date)
    second_id = _create("ConfirmedB2", status="Confirmed", move_date=move_date)
    row = _booking_row(second_id)
    payload = _payload_from_row(row, status="Confirmed")
    errors, conflicts, _ = double_booking.validate_save(
        payload, booking_id=second_id, override_confirmed=False
    )
    ctx = double_booking.ui_context(row, payload)

    passed = bool(errors) and bool(conflicts) and ctx["show_double_booking_banner"]
    return {
        "name": "Test B — Confirmed overlaps Confirmed",
        "passed": passed,
        "details": {
            "first_id": first_id,
            "second_id": second_id,
            "errors": errors,
            "conflict_count": len(conflicts),
            "show_banner": ctx["show_double_booking_banner"],
            "checked_logs": len(_logs(second_id, automation.AUTOMATION_DOUBLE_BOOKING_CHECKED)),
            "conflict_logs": len(_logs(second_id, automation.AUTOMATION_DOUBLE_BOOKING_CONFLICT)),
        },
    }


def test_c(move_date: str) -> dict:
    _create("ConfirmedC", status="Confirmed", move_date=move_date)
    pending_id = _create("PendingC", status="Pending", move_date=move_date)
    row = _booking_row(pending_id)
    payload = _payload_from_row(row, status="Confirmed")

    block_errors, conflicts, _ = double_booking.validate_save(
        payload, booking_id=pending_id, override_confirmed=False
    )
    ok_errors, _, override_applied = double_booking.validate_save(
        payload, booking_id=pending_id, override_confirmed=True
    )
    db.update_booking_status(pending_id, "Confirmed")
    after = _booking_row(pending_id)

    passed = (
        bool(block_errors)
        and bool(conflicts)
        and not ok_errors
        and override_applied
        and bool(after.get("double_booking_override_at"))
        and bool(_logs(pending_id, automation.AUTOMATION_DOUBLE_BOOKING_OVERRIDE))
    )
    return {
        "name": "Test C — Pending → Confirmed with overlap",
        "passed": passed,
        "details": {
            "pending_id": pending_id,
            "block_errors": block_errors,
            "override_applied": override_applied,
            "override_at": after.get("double_booking_override_at"),
        },
    }


def test_d(move_date: str) -> dict:
    _create("ConfirmedD1", status="Confirmed", move_date=move_date, start_time="09:00")
    clear_id = _create(
        "ConfirmedD2",
        status="Confirmed",
        move_date=move_date,
        start_time="14:00",
        duration_hours="2",
    )
    row = _booking_row(clear_id)
    payload = _payload_from_row(row, status="Confirmed")
    errors, conflicts, _ = double_booking.validate_save(
        payload, booking_id=clear_id, override_confirmed=False
    )
    badge = double_booking.badge_for_booking(row)

    passed = not errors and not conflicts and badge == "clear"
    return {
        "name": "Test D — Confirmed not overlapping",
        "passed": passed,
        "details": {
            "booking_id": clear_id,
            "errors": errors,
            "conflict_count": len(conflicts),
            "badge": badge,
        },
    }


def test_e(move_date: str) -> dict:
    confirmed_id = _create("ConfirmedE", status="Confirmed", move_date=move_date)
    cancelled_id = _create("CancelledE", status="Cancelled", move_date=move_date)
    confirmed = _booking_row(confirmed_id)
    conflicts = double_booking.find_conflicts(
        double_booking.booking_payload_from_form(
            _payload_from_row(confirmed), confirmed_id
        ),
        exclude_booking_id=confirmed_id,
    )
    cancelled_in_conflicts = any(c["id"] == cancelled_id for c in conflicts)

    passed = not cancelled_in_conflicts
    return {
        "name": "Test E — Cancelled overlap ignored",
        "passed": passed,
        "details": {
            "confirmed_id": confirmed_id,
            "cancelled_id": cancelled_id,
            "conflict_count": len(conflicts),
            "cancelled_listed": cancelled_in_conflicts,
        },
    }


def main() -> int:
    db.init_db()
    tests = [
        test_a(_move_date(60)),
        test_b(_move_date(61)),
        test_c(_move_date(62)),
        test_d(_move_date(63)),
        test_e(_move_date(64)),
    ]
    all_passed = all(bool(t["passed"]) for t in tests)
    results = {
        "tests": tests,
        "summary_table": [
            {"test": t["name"], "result": "PASS" if t["passed"] else "FAIL"}
            for t in tests
        ],
        "passed": all_passed,
    }

    out_path = RESULTS_DIR / "phase14_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print("")
    print("FINAL RESULTS")
    print("Test | Result")
    print("-----|-------")
    for row in results["summary_table"]:
        print("{0} | {1}".format(row["test"], row["result"]))
    print("")
    print("OVERALL:", "PASS" if all_passed else "FAIL")
    print("RESULTS_FILE", out_path)
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
