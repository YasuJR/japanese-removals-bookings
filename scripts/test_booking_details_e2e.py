#!/usr/bin/env python3
"""E2E tests for read-only booking details page and dashboard Details button."""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth
import database as db
from app import app

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "booking-details-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(
        label,
        auth.hash_password("test"),
        "Booking Details Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _sample_booking_id():
    booking_id = db.create_booking(
        customer_name="Details Test Customer",
        phone="0412 345 678",
        email="details@example.com",
        pickup_address="10 Pickup Rd, Subiaco WA 6008",
        delivery_address="20 Delivery St, Fremantle WA 6160",
        move_date="2026-08-20",
        num_movers=2,
        notes="Handle with care",
        start_time="08:00",
        finish_time="11:00",
        duration_hours="3",
        crew="Ken, Toshi",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        status="Confirmed",
    )
    db.update_booking_integration_fields(
        booking_id, {"truck_assigned": "Truck 1"}
    )
    return booking_id


def test_dashboard_jobs_table_has_details_and_edit_buttons():
    client = _login_client()
    booking_id = _sample_booking_id()
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Details Test Customer" in html
    assert 'href="/bookings/{0}"'.format(booking_id) in html
    assert ">Details</a>" in html
    assert ">Edit</a>" in html
    assert "dashboard-job-cards" not in html
    assert "dashboard-sheet" in html
    return True


def test_view_booking_page_shows_correct_booking():
    client = _login_client()
    booking_id = _sample_booking_id()
    resp = client.get("/bookings/{0}".format(booking_id))
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Details Test Customer" in html
    assert "Booking #{0}".format(booking_id) in html
    assert "0412 345 678" in html
    assert "details@example.com" in html
    assert "10 Pickup Rd, Subiaco WA 6008" in html
    assert "20 Delivery St, Fremantle WA 6160" in html
    assert "3 Hours" in html or "3.0 Hours" in html
    return True


def test_view_booking_page_is_read_only():
    client = _login_client()
    booking_id = _sample_booking_id()
    html = client.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    assert '<form method="post"' not in html
    assert 'name="customer_name"' not in html
    assert 'type="submit"' not in html
    return True


def test_view_booking_page_shows_pricing_and_status_fields():
    client = _login_client()
    booking_id = _sample_booking_id()
    html = client.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    for label in (
        "Hourly rate",
        "Callout fee",
        "Extra charges",
        "GST",
        "Total invoice amount",
        "Payment status",
        "Invoice status",
        "Truck assigned",
        "Crew",
        "Number of movers",
    ):
        assert label in html, label
    assert "Truck 1" in html
    assert "Ken" in html
    return True


def test_view_booking_action_links():
    client = _login_client()
    booking_id = _sample_booking_id()
    html = client.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    assert 'href="tel:' in html
    assert 'href="sms:' in html
    assert "Pickup Map" in html
    assert "Delivery Map" in html
    assert "maps.apple.com" in html
    assert 'href="/bookings/{0}/edit"'.format(booking_id) in html
    assert ">Edit Booking</a>" in html
    assert 'href="/dashboard"' in html
    assert ">Back to Dashboard</a>" in html
    return True


def test_edit_booking_still_works():
    client = _login_client()
    booking_id = _sample_booking_id()
    resp = client.get("/bookings/{0}/edit".format(booking_id))
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Edit booking #{0}".format(booking_id) in html
    assert 'name="customer_name"' in html
    assert "Details Test Customer" in html
    return True


def test_view_booking_mobile_layout():
    client = _login_client()
    booking_id = _sample_booking_id()
    html = client.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    assert "booking-details-page" in html
    assert "booking-details-grid" in html
    assert "mobile.css" in html
    assert "touch-action-btn" in html
    mobile_css = (ROOT / "static" / "mobile.css").read_text()
    assert ".booking-details-page" in mobile_css
    assert ".booking-details-footer-actions .touch-action-btn" in mobile_css
    match = re.search(r"--touch:\s*(\d+)px", mobile_css)
    assert match and int(match.group(1)) >= 44
    return True


def test_view_booking_not_found_redirects():
    client = _login_client()
    resp = client.get("/bookings/999999999")
    assert resp.status_code == 302
    assert "/bookings/all" in resp.headers.get("Location", "")
    return True


def main():
    tests = [
        test_dashboard_jobs_table_has_details_and_edit_buttons,
        test_view_booking_page_shows_correct_booking,
        test_view_booking_page_is_read_only,
        test_view_booking_page_shows_pricing_and_status_fields,
        test_view_booking_action_links,
        test_edit_booking_still_works,
        test_view_booking_mobile_layout,
        test_view_booking_not_found_redirects,
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
