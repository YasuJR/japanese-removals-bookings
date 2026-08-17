#!/usr/bin/env python3
"""E2E tests — editable finish time on Edit Booking."""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import invoice
import services
from app import app
from booking_times import validate_times
from validators import parse_booking_form


_test_user_counter = 0


def _unique_move_date():
    global _test_user_counter
    day = 20 + (_test_user_counter % 8)
    return "2026-09-{0:02d}".format(day)


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "finish-time-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Finish Time Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_booking():
    move_date = _unique_move_date()
    booking_id = db.create_booking(
        "Finish Time Customer",
        "0412000333",
        "finish@example.com",
        "1 Start St, Perth WA",
        "2 End Ave, Fremantle WA",
        move_date,
        2,
        "Finish time edit test",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        start_time="08:00",
        finish_time="11:00",
        duration_hours="3",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
    )
    return booking_id, move_date


def test_edit_page_has_finish_time_input():
    booking_id, _move_date = _create_booking()
    client = _login_client()
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert re.search(
        r'<input[^>]+type="time"[^>]+name="finish_time"[^>]+value="11:00"',
        html,
    ), "Expected editable finish time input with initial value"
    assert 'id="finish_live_text"' not in html
    return True


def test_finish_time_change_updates_duration_and_invoice():
    booking_id, move_date = _create_booking()
    client = _login_client()
    form = {
        "customer_name": "Finish Time Customer",
        "phone": "0412000333",
        "email": "finish@example.com",
        "pickup_address": "1 Start St, Perth WA",
        "delivery_address": "2 End Ave, Fremantle WA",
        "move_date": move_date,
        "num_movers": "2",
        "notes": "Finish time edit test",
        "start_time": "08:00",
        "finish_time": "13:00",
        "duration_hours": "3",
        "hourly_rate": "180",
        "callout_fee": "90",
        "gst_enabled": "on",
        "payment_status": "Unpaid",
        "invoice_status": "",
        "status": "Completed",
        "action": "save",
    }
    resp = client.post("/bookings/{0}/edit".format(booking_id), data=form, follow_redirects=False)
    assert resp.status_code in (302, 303), resp.status_code
    row = dict(db.get_booking(booking_id))
    assert row.get("finish_time") == "13:00"
    assert row.get("duration_hours") == "5"
    totals = invoice.calculate_invoice_totals(services.booking_to_dict(row))
    assert totals["hours"] == 5.0
    assert totals["total"] == 990.0
    return True


def test_validate_times_prefers_explicit_finish_over_duration():
    start, finish, duration, errors = validate_times("08:00", "12:00", "3")
    assert not errors
    assert finish == "12:00"
    assert duration == "4"
    return True


def main():
    tests = [
        test_edit_page_has_finish_time_input,
        test_finish_time_change_updates_duration_and_invoice,
        test_validate_times_prefers_explicit_finish_over_duration,
    ]
    passed = 0
    for test in tests:
        try:
            if test():
                print("PASS:", test.__name__)
                passed += 1
            else:
                print("FAIL:", test.__name__)
        except Exception as exc:
            print("FAIL:", test.__name__, "—", exc)
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
