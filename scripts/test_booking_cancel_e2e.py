#!/usr/bin/env python3
"""Regression tests for booking cancellation flow."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth
import database as db
from app import app
from integrations import google_calendar

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "cancel-flow-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(
        label,
        auth.hash_password("test"),
        "Cancel Flow Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_booking(**overrides):
    data = {
        "customer_name": "Cancel Flow Customer",
        "phone": "0412333444",
        "email": "cancel-flow@example.com",
        "pickup_address": "5 Pickup Ave, Perth WA 6000",
        "delivery_address": "9 Delivery Rd, Fremantle WA 6160",
        "move_date": "2026-09-10",
        "num_movers": 2,
        "notes": "Original booking notes",
        "start_time": "08:00",
        "finish_time": "11:00",
        "duration_hours": "3",
        "crew": "Ken",
        "hourly_rate": 180.0,
        "callout_fee": 90.0,
        "gst_enabled": 1,
        "status": "Confirmed",
    }
    data.update(overrides)
    booking_id = db.create_booking(
        data["customer_name"],
        data["phone"],
        data["email"],
        data["pickup_address"],
        data["delivery_address"],
        data["move_date"],
        data["num_movers"],
        data["notes"],
        start_time=data["start_time"],
        finish_time=data["finish_time"],
        duration_hours=data["duration_hours"],
        crew=data["crew"],
        hourly_rate=data["hourly_rate"],
        callout_fee=data["callout_fee"],
        gst_enabled=data["gst_enabled"],
        status=data["status"],
    )
    return booking_id


def _edit_form(booking_id, **overrides):
    row = db.get_booking(booking_id)
    form = {
        "action": "save",
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "email": row["email"],
        "pickup_address": row["pickup_address"],
        "delivery_address": row["delivery_address"],
        "move_date": row["move_date"],
        "status": row["status"],
        "start_time": row["start_time"] or "08:00",
        "finish_time": row["finish_time"] or "11:00",
        "duration_hours": row["duration_hours"] or "3",
        "num_movers": str(row["num_movers"]),
        "notes": row["notes"] or "",
        "hourly_rate": str(row["hourly_rate"] or 180),
        "callout_fee": str(row["callout_fee"] or 90),
        "gst_enabled": "on" if row["gst_enabled"] else "",
        "payment_status": row["payment_status"] or "Unpaid",
        "invoice_status": row["invoice_status"] or "",
        "truck_assigned": row["truck_assigned"] or "",
    }
    form.update(overrides)
    return form


def test_cancel_normal_booking_does_not_500():
    client = _login_client()
    booking_id = _create_booking()
    before_count = len(db.list_all())
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_edit_form(booking_id, status="Cancelled"),
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)[:500]
    row = db.get_booking(booking_id)
    assert row is not None
    assert row["status"] == "Cancelled"
    assert row["customer_name"] == "Cancel Flow Customer"
    assert row["phone"] == "0412333444"
    assert row["notes"] == "Original booking notes"
    assert len(db.list_all()) == before_count
    return True


def test_cancel_booking_with_calendar_event_calls_delete_not_sync():
    client = _login_client()
    booking_id = _create_booking()
    db.update_booking_integration_fields(
        booking_id, {"google_calendar_event_id": "evt-cancel-test"}
    )
    with patch.object(
        google_calendar, "sync_booking_to_calendar", return_value="SHOULD NOT SYNC"
    ) as sync_mock, patch.object(
        google_calendar,
        "cancel_booking_calendar_event",
        return_value="Removed from Google Calendar.",
    ) as cancel_mock:
        resp = client.post(
            "/bookings/{0}/edit".format(booking_id),
            data=_edit_form(booking_id, status="Cancelled"),
            follow_redirects=False,
        )
    assert resp.status_code == 302
    sync_mock.assert_not_called()
    cancel_mock.assert_called_once()
    assert db.get_booking(booking_id)["status"] == "Cancelled"
    return True


def test_cancel_booking_without_calendar_event():
    client = _login_client()
    booking_id = _create_booking()
    with patch.object(
        google_calendar, "cancel_booking_calendar_event", return_value=None
    ) as cancel_mock:
        resp = client.post(
            "/bookings/{0}/edit".format(booking_id),
            data=_edit_form(booking_id, status="Cancelled"),
            follow_redirects=False,
        )
    assert resp.status_code == 302
    cancel_mock.assert_called_once()
    row = db.get_booking(booking_id)
    assert not (row["google_calendar_event_id"] or "")
    return True


def test_cancel_already_cancelled_booking_is_idempotent():
    client = _login_client()
    booking_id = _create_booking(status="Cancelled")
    with patch.object(
        google_calendar,
        "cancel_booking_calendar_event",
        return_value="Calendar removal failed: Not Found",
    ):
        resp = client.post(
            "/bookings/{0}/edit".format(booking_id),
            data=_edit_form(booking_id, status="Cancelled", notes="Still cancelled"),
            follow_redirects=False,
        )
    assert resp.status_code == 302
    row = db.get_booking(booking_id)
    assert row["status"] == "Cancelled"
    assert row["notes"] == "Still cancelled"
    return True


def test_calendar_refresh_failure_does_not_crash_cancellation():
    client = _login_client()
    booking_id = _create_booking()
    db.update_booking_integration_fields(
        booking_id, {"google_calendar_event_id": "evt-bad-token"}
    )

    def _raise_refresh(*_args, **_kwargs):
        from google.auth.exceptions import RefreshError

        raise RefreshError("invalid_grant: Bad Request")

    with patch(
        "integrations.google_oauth.get_credentials", side_effect=_raise_refresh
    ):
        resp = client.post(
            "/bookings/{0}/edit".format(booking_id),
            data=_edit_form(booking_id, status="Cancelled"),
            follow_redirects=False,
        )
    assert resp.status_code == 302, resp.get_data(as_text=True)[:500]
    row = db.get_booking(booking_id)
    assert row["status"] == "Cancelled"
    assert row["customer_name"] == "Cancel Flow Customer"
    return True


def test_existing_booking_data_remains_intact_after_cancel():
    client = _login_client()
    booking_id = _create_booking()
    db.update_booking_integration_fields(
        booking_id,
        {
            "truck_assigned": "Truck 1",
            "google_calendar_event_id": "evt-keep-history",
        },
    )
    db.update_booking_invoice_fields(
        booking_id,
        {
            "invoice_number": "INV-1001",
            "payment_status": "Unpaid",
            "xero_invoice_id": "xero-test-id",
        },
    )
    before = dict(db.get_booking(booking_id))
    before_count = len(db.list_all())

    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_edit_form(booking_id, status="Cancelled"),
        follow_redirects=False,
    )
    assert resp.status_code == 302
    after = dict(db.get_booking(booking_id))
    assert len(db.list_all()) == before_count
    assert after["id"] == booking_id
    assert after["status"] == "Cancelled"
    assert after["customer_name"] == before["customer_name"]
    assert after["phone"] == before["phone"]
    assert after["email"] == before["email"]
    assert after["pickup_address"] == before["pickup_address"]
    assert after["delivery_address"] == before["delivery_address"]
    assert after["invoice_number"] == before["invoice_number"]
    assert after["xero_invoice_id"] == before["xero_invoice_id"]
    assert after["payment_status"] == before["payment_status"]
    assert after["google_calendar_event_id"] == before["google_calendar_event_id"]
    assert after["truck_assigned"] == before["truck_assigned"]
    return True


def main():
    tests = [
        test_cancel_normal_booking_does_not_500,
        test_cancel_booking_with_calendar_event_calls_delete_not_sync,
        test_cancel_booking_without_calendar_event,
        test_cancel_already_cancelled_booking_is_idempotent,
        test_calendar_refresh_failure_does_not_crash_cancellation,
        test_existing_booking_data_remains_intact_after_cancel,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS:", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", fn.__name__, exc)
    print("\n{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
