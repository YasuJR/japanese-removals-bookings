#!/usr/bin/env python3
"""Truck-aware double booking: different trucks must not require override."""

from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SECRET_KEY", "test-truck-double-booking")

import database as db
import double_booking
from resource_conflicts import (
    find_crew_conflict_warnings,
    find_truck_conflict_warnings,
    has_crew_conflict,
    has_truck_conflict,
)


def _unique_move_date(tag: str) -> str:
    seed = hash("{0}-{1}".format(tag, time.time_ns())) % 400
    return (date.today() + timedelta(days=350 + seed)).isoformat()


def _create(
    label: str,
    *,
    move_date: str,
    start_time: str = "08:00",
    duration_hours: str = "4",
    truck: str = "",
    crew: str = "Yasu",
    status: str = "Confirmed",
) -> int:
    booking_id = db.create_booking(
        "{0} Customer".format(label),
        "0412000888",
        "{0}@example.com".format(label.lower().replace(" ", "")),
        "1 Truck St, Perth WA",
        "2 Truck Ave, Fremantle WA",
        move_date,
        2,
        label,
        start_time=start_time,
        duration_hours=duration_hours,
        crew=crew,
        status=status,
    )
    if truck:
        db.update_booking_integration_fields(booking_id, {"truck_assigned": truck})
    if status != "Pending":
        db.update_booking_status(booking_id, status)
    return booking_id


def _payload(row: dict, booking_id: int) -> dict:
    return double_booking.booking_payload_from_form(
        {
            "move_date": row["move_date"],
            "start_time": row["start_time"] or "08:00",
            "finish_time": row.get("finish_time") or "",
            "duration_hours": row.get("duration_hours") or "4",
            "status": row.get("status") or "Confirmed",
            "customer_name": row.get("customer_name") or "",
            "truck_assigned": row.get("truck_assigned") or "",
            "crew": row.get("crew") or "",
        },
        booking_id,
    )


def test_case_a_different_trucks_no_override():
    move_day = _unique_move_date("A")
    _create("A1", move_date=move_day, truck="Truck 1")
    second_id = _create("A2", move_date=move_day, truck="Truck 2")
    row = dict(db.get_booking(second_id))
    errors, conflicts, _ = double_booking.validate_save(
        _payload(row, second_id), booking_id=second_id
    )
    assert not errors and not conflicts
    assert double_booking.badge_for_booking(row) == "clear"
    assert not has_truck_conflict(row, exclude_booking_id=second_id)
    return True


def test_case_b_same_truck_overlap():
    move_day = _unique_move_date("B")
    _create("B1", move_date=move_day, truck="Truck 1", start_time="08:00")
    second_id = _create(
        "B2",
        move_date=move_day,
        truck="Truck 1",
        start_time="09:00",
        duration_hours="2",
    )
    row = dict(db.get_booking(second_id))
    errors, conflicts, _ = double_booking.validate_save(
        _payload(row, second_id), booking_id=second_id
    )
    assert errors and conflicts
    assert has_truck_conflict(row, exclude_booking_id=second_id)
    return True


def test_case_c_same_truck_back_to_back():
    move_day = _unique_move_date("C")
    _create(
        "C1",
        move_date=move_day,
        truck="Truck 2",
        start_time="08:00",
        duration_hours="4",
    )
    second_id = _create(
        "C2",
        move_date=move_day,
        truck="Truck 2",
        start_time="12:00",
        duration_hours="3",
    )
    row = dict(db.get_booking(second_id))
    errors, conflicts, _ = double_booking.validate_save(
        _payload(row, second_id), booking_id=second_id
    )
    assert not errors and not conflicts
    return True


def test_case_d_crew_conflict_different_trucks():
    move_day = _unique_move_date("D")
    _create("D1", move_date=move_day, truck="Truck 1", crew="Yasu")
    second_id = _create("D2", move_date=move_day, truck="Truck 2", crew="Yasu")
    row = dict(db.get_booking(second_id))
    assert not has_truck_conflict(row, exclude_booking_id=second_id)
    assert has_crew_conflict(row, exclude_booking_id=second_id)
    assert find_crew_conflict_warnings(row, exclude_booking_id=second_id)
    errors, conflicts, _ = double_booking.validate_save(
        _payload(row, second_id), booking_id=second_id
    )
    assert not errors and not conflicts
    return True


def test_case_e_pending_excluded():
    move_day = _unique_move_date("E")
    _create("E1", move_date=move_day, truck="Truck 1", status="Confirmed")
    pending_id = _create("E2", move_date=move_day, truck="Truck 1", status="Pending")
    row = dict(db.get_booking(pending_id))
    errors, conflicts, _ = double_booking.validate_save(
        _payload(row, pending_id), booking_id=pending_id
    )
    assert not errors
    assert not find_truck_conflict_warnings(row, exclude_booking_id=pending_id)
    return True


def main() -> int:
    db.init_db()
    tests = [
        test_case_a_different_trucks_no_override,
        test_case_b_same_truck_overlap,
        test_case_c_same_truck_back_to_back,
        test_case_d_crew_conflict_different_trucks,
        test_case_e_pending_excluded,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS:", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", fn.__name__, exc)
    print("{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
