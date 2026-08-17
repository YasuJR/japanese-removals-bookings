#!/usr/bin/env python3
"""E2E tests — inline payment changes on Dashboard jobs table."""

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import invoice
from app import app
from dashboard_data import dashboard_jobs


_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "dashboard-payment-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Dashboard Payment Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _create_booking(move_date="2026-10-01"):
    return db.create_booking(
        "Dashboard Payment Customer",
        "0412000777",
        "dash-payment@example.com",
        "1 Pay St, Perth WA",
        "2 Pay Ave, Fremantle WA",
        move_date,
        2,
        "dashboard payment test",
        status="Confirmed",
    )


def test_dashboard_renders_payment_picker():
    booking_id = _create_booking()
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    assert 'class="dashboard-payment-picker"' in html
    assert 'data-booking-id="{0}"'.format(booking_id) in html
    assert 'id="dashboard-payment-portal"' in html
    assert 'id="dashboard-payment-options-json"' in html
    assert "dashboard_payment.js" in html
    assert html.count('class="dashboard-payment-option"') == 0
    for option in invoice.DASHBOARD_INLINE_PAYMENT_OPTIONS:
        assert option in html
    return True


def test_inline_payment_update_persists():
    booking_id = _create_booking()
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/payment".format(booking_id),
        json={"payment_status": "Paid"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["payment_status"] == "Paid"
    assert payload["css_class"] == "paid"
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["paid_at"]
    return True


def test_inline_payment_can_revert_to_unpaid():
    booking_id = _create_booking()
    client = _login_client()
    client.post(
        "/bookings/{0}/payment".format(booking_id),
        json={"payment_status": "Paid"},
    )
    resp = client.post(
        "/bookings/{0}/payment".format(booking_id),
        json={"payment_status": "Unpaid"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["payment_status"] == "Unpaid"
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Unpaid"
    assert not (row.get("paid_at") or "").strip()
    return True


def test_invalid_payment_status_rejected():
    booking_id = _create_booking()
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/payment".format(booking_id),
        json={"payment_status": "Part Paid"},
    )
    assert resp.status_code == 400
    return True


def test_dashboard_shows_updated_payment_after_reload():
    customer = "Dashboard Payment Reload {0}".format(os.getpid())
    move_date = date.today().isoformat()
    booking_id = db.create_booking(
        customer,
        "0412000666",
        "pay-reload@example.com",
        "1 Reload Pay St, Perth WA",
        "2 Reload Pay Ave, Fremantle WA",
        move_date,
        2,
        "payment reload test",
        status="Confirmed",
    )
    client = _login_client()
    client.post(
        "/bookings/{0}/payment".format(booking_id),
        json={"payment_status": "Paid"},
    )
    jobs_total = len(dashboard_jobs("all", date.today()))
    html = client.get(
        "/dashboard?filter=all&jobs_limit={0}".format(max(jobs_total, 1))
    ).get_data(as_text=True)
    assert customer in html
    idx = html.find(
        'dashboard-payment-picker" data-booking-id="{0}"'.format(booking_id)
    )
    assert idx >= 0, "Expected booking on dashboard"
    window = html[idx : idx + 800]
    assert 'dashboard-payment-label">Paid<' in window
    return True


def test_dashboard_rows_do_not_embed_payment_menus():
    db.init_db()
    for idx in range(3):
        _create_booking(move_date="2098-02-{0:02d}".format(idx + 1))
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    assert html.count("dashboard-payment-picker") >= 3
    assert html.count('class="dashboard-payment-menu"') == 1
    assert html.count('class="dashboard-payment-option"') == 0
    return True


def main():
    tests = [
        test_dashboard_renders_payment_picker,
        test_dashboard_rows_do_not_embed_payment_menus,
        test_inline_payment_update_persists,
        test_inline_payment_can_revert_to_unpaid,
        test_invalid_payment_status_rejected,
        test_dashboard_shows_updated_payment_after_reload,
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
