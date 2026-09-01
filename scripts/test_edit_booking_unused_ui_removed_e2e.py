#!/usr/bin/env python3
"""E2E — unused Booking Edit UI is not rendered."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import invoice
from app import app


_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "edit-ui-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Edit UI Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_booking(**kwargs):
    return db.create_booking(
        kwargs.get("customer_name", "Unused UI Customer"),
        kwargs.get("phone", "0412333444"),
        kwargs.get("email", "unused-ui@example.com"),
        "1 Simple St, Perth WA",
        "2 Simple Ave, Fremantle WA",
        kwargs.get("move_date", "2026-09-18"),
        2,
        "unused ui removed",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        start_time="08:00",
        finish_time="10:00",
        duration_hours="2",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
        crew="Yasu",
        status=kwargs.get("status", "Confirmed"),
    )


def _form(booking_id, **overrides):
    row = dict(db.get_booking(booking_id))
    base = {
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "email": row["email"],
        "pickup_address": row["pickup_address"],
        "delivery_address": row["delivery_address"],
        "move_date": row["move_date"],
        "num_movers": str(row["num_movers"]),
        "notes": row["notes"] or "",
        "start_time": row["start_time"] or "08:00",
        "finish_time": row["finish_time"] or "10:00",
        "duration_hours": row["duration_hours"] or "2",
        "hourly_rate": str(row["hourly_rate"] if row["hourly_rate"] is not None else 180),
        "callout_fee": str(row["callout_fee"] if row["callout_fee"] is not None else 90),
        "gst_enabled": "on",
        "payment_status": row["payment_status"] or "Unpaid",
        "invoice_status": row["invoice_status"] or "",
        "status": row["status"] or "Confirmed",
        "crew": row["crew"] or "Yasu",
        "action": "save",
    }
    base.update(overrides)
    return base


REMOVED = (
    "Sync calendar",
    "Create Xero Invoice",
    "Update Xero Invoice",
    "Check Xero payment status",
    "Send booking confirmation SMS",
    "Send payment reminder SMS",
    "Send thank you SMS",
    "Confirmation SMS sent",
    "Calendar synced",
    "Staff notified",
    "Driver on route",
    "Driver name",
    "Manual ETA",
    "Driver origin",
    "Job Costs",
    "Staff Cost",
    "Fuel Cost",
    "Truck Cost",
    "Parking Cost",
    "Other Cost",
    "Total Job Cost",
    'name="staff_cost"',
    'name="fuel_cost"',
    'name="truck_cost"',
    'name="parking_cost"',
    'name="other_costs"',
    "on-route-panel",
    "phase9-automation-status",
    "phase10-automation-status",
)


def test_edit_page_does_not_render_unused_ui():
    booking_id = _create_booking()
    db.update_booking_integration_fields(
        booking_id,
        {
            "sms_booking_confirmed_sent_at": "2026-08-01 09:00:00",
            "calendar_confirmed_synced_at": "2026-08-01 09:01:00",
            "staff_notification_sent_at": "2026-08-01 09:02:00",
            "on_route_at": "2026-08-01 08:00:00",
            "eta_sms_sent_at": "2026-08-01 08:01:00",
            "eta_minutes": 20,
            "driver_name": "Yasu",
        },
    )
    client = _login_client()
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "Mark as Paid" in html
    assert "Mark as Unpaid" in html
    assert "Download PDF" in html
    assert "Save Changes" in html
    assert "Delete booking" in html
    assert "Invoice overrides" in html
    for label in REMOVED:
        assert label not in html, label
    return True


def test_save_without_job_costs_keeps_existing_costs():
    booking_id = _create_booking()
    db.update_booking_profit_fields(
        booking_id,
        {
            "staff_cost": 144.0,
            "fuel_cost": 25.0,
            "truck_cost": 40.0,
            "parking_cost": 10.0,
            "other_costs": 5.0,
        },
    )
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(booking_id, customer_name="Costs Kept"),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    row = dict(db.get_booking(booking_id))
    assert row["customer_name"] == "Costs Kept"
    assert round(float(row["staff_cost"] or 0), 2) == 144.0
    assert round(float(row["fuel_cost"] or 0), 2) == 25.0
    assert round(float(row["truck_cost"] or 0), 2) == 40.0
    assert round(float(row["parking_cost"] or 0), 2) == 10.0
    assert round(float(row["other_costs"] or 0), 2) == 5.0
    return True


def test_new_booking_and_view_still_have_job_costs():
    booking_id = _create_booking()
    db.update_booking_profit_fields(booking_id, {"staff_cost": 144.0, "fuel_cost": 25.0})
    client = _login_client()
    new_html = client.get("/bookings/new").get_data(as_text=True)
    assert "Job Costs" in new_html
    assert 'name="staff_cost"' in new_html
    view_html = client.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    assert "Job Costs" in view_html
    assert "Staff Cost" in view_html
    return True


def main():
    db.init_db()
    tests = [
        test_edit_page_does_not_render_unused_ui,
        test_save_without_job_costs_keeps_existing_costs,
        test_new_booking_and_view_still_have_job_costs,
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
