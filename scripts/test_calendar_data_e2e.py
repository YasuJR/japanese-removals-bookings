#!/usr/bin/env python3
"""Regression tests for internal booking calendar data."""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import calendar_data
import database as db
from app import app


def _hao_style_booking(
    *,
    booking_id: int = 16,
    move_date=date(2026, 8, 11),
    status: str = "Confirmed",
) -> dict:
    return {
        "id": booking_id,
        "customer_name": "Hao",
        "move_date": move_date,
        "start_time": "10:00",
        "finish_time": "14:00",
        "duration_hours": "4",
        "num_movers": 2,
        "crew": "Keiichi, Yasu",
        "status": status,
        "payment_status": "Unpaid",
        "truck_assigned": "Truck 1",
        "phone": "0400000000",
        "email": "hao@example.com",
        "pickup_address": "1 Pickup St",
        "delivery_address": "2 Delivery Ave",
        "notes": "",
    }


class FakeRow:
    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def get(self, key, default=None):
        return self._data.get(key, default)


def test_confirmed_booking_appears_on_calendar():
    db.init_db()
    move_day = "2026-10-05"
    booking_id = db.create_booking(
        "Calendar Confirmed Test",
        "0412000888",
        "cal-confirmed@example.com",
        "1 Cal St, Perth WA",
        "2 Cal Ave, Fremantle WA",
        move_day,
        2,
        "Calendar confirmed regression",
        start_time="09:00",
        finish_time="12:00",
        duration_hours="3",
        status="Confirmed",
        crew="Ken",
    )
    ctx = calendar_data.build_calendar_context(year=2026, month=10, view="month")
    ids = {event["id"] for event in ctx["events"]}
    assert booking_id in ids, ids
    assert ctx["events_by_date"][move_day]
    return True


def test_quote_booking_appears_on_calendar():
    db.init_db()
    move_day = "2026-10-06"
    booking_id = db.create_booking(
        "Calendar Quote Test",
        "0412000777",
        "cal-quote@example.com",
        "3 Quote St, Perth WA",
        "4 Quote Ave, Fremantle WA",
        move_day,
        2,
        "Calendar quote regression",
        status="Quote",
    )
    ctx = calendar_data.build_calendar_context(year=2026, month=10, view="month")
    event = next(e for e in ctx["events"] if e["id"] == booking_id)
    assert event["status"] == "Quote"
    assert event["status_class"] == "cal-quote"
    return True


def test_cancelled_booking_appears_with_cancelled_class():
    db.init_db()
    move_day = "2026-10-07"
    booking_id = db.create_booking(
        "Calendar Cancelled Test",
        "0412000666",
        "cal-cancel@example.com",
        "5 Cancel St, Perth WA",
        "6 Cancel Ave, Fremantle WA",
        move_day,
        2,
        "Calendar cancelled regression",
        status="Cancelled",
    )
    ctx = calendar_data.build_calendar_context(year=2026, month=10, view="month")
    event = next(e for e in ctx["events"] if e["id"] == booking_id)
    assert event["status"] == "Cancelled"
    assert event["status_class"] == "cal-cancelled"
    return True


def test_postgres_date_and_datetime_values_render():
    booking = _hao_style_booking(move_date=date(2026, 8, 11))
    event = calendar_data.calendar_event(booking)
    assert event["move_date"] == "2026-08-11"
    assert event["customer_name"] == "Hao"

    booking_dt = _hao_style_booking(booking_id=17, move_date=datetime(2026, 8, 11, 0, 0, 0))
    event_dt = calendar_data.calendar_event(booking_dt)
    assert event_dt["move_date"] == "2026-08-11"
    return True


def test_hao_style_booking_on_correct_calendar_day():
    row = FakeRow(_hao_style_booking())
    original = db.list_between_dates
    db.list_between_dates = lambda start, end: (
        [row] if start <= "2026-08-11" <= end else []
    )
    try:
        ctx = calendar_data.build_calendar_context(year=2026, month=8, view="month")
        day_events = ctx["events_by_date"].get("2026-08-11", [])
        assert any(event["customer_name"] == "Hao" for event in day_events)
        assert any(event["id"] == 16 for event in day_events)
    finally:
        db.list_between_dates = original
    return True


def test_calendar_page_renders_postgres_move_date():
    db.init_db()
    row = FakeRow(_hao_style_booking(booking_id=901))
    original = db.list_between_dates
    db.list_between_dates = lambda start, end: [row]
    try:
        client = app.test_client()
        db.init_db()
        username = "cal-page-{0}".format(datetime.now().timestamp())
        uid = db.create_staff_user(
            username,
            __import__("auth").hash_password("test-password"),
            "Calendar Page",
            is_admin=1,
        )
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        resp = client.get("/calendar?view=month&year=2026&month=8")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200, resp.status_code
        assert "Hao" in html
        assert "2026-08-11" in html
    finally:
        db.list_between_dates = original
    return True


def main() -> int:
    tests = [
        test_confirmed_booking_appears_on_calendar,
        test_quote_booking_appears_on_calendar,
        test_cancelled_booking_appears_with_cancelled_class,
        test_postgres_date_and_datetime_values_render,
        test_hao_style_booking_on_correct_calendar_day,
        test_calendar_page_renders_postgres_move_date,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print("PASS:", test.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", test.__name__, "—", exc)
    print("\n{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
