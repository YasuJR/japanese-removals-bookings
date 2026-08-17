#!/usr/bin/env python3
"""E2E tests — inline status changes on Dashboard jobs table."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import job_status
from app import app


_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "dashboard-status-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Dashboard Status Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _create_booking(status="Confirmed", move_date="2026-10-01"):
    return db.create_booking(
        "Dashboard Status Customer",
        "0412000999",
        "dash-status@example.com",
        "1 Dash St, Perth WA",
        "2 Dash Ave, Fremantle WA",
        move_date,
        2,
        "dashboard status test",
        status=status,
    )


def test_dashboard_renders_status_picker():
    booking_id = _create_booking()
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    assert 'class="dashboard-status-picker"' in html
    assert 'data-booking-id="{0}"'.format(booking_id) in html
    assert 'id="dashboard-status-portal"' in html
    assert 'id="dashboard-status-options-json"' in html
    assert "dashboard_status.js" in html
    assert html.count('class="dashboard-status-option"') == 0
    for option in job_status.DASHBOARD_INLINE_STATUS_OPTIONS:
        assert option in html
    return True


def test_inline_status_update_persists():
    booking_id = _create_booking(status="Confirmed")
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/status".format(booking_id),
        json={"status": "Invoiced"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["status"] == "Invoiced"
    assert payload["css_class"] == "invoiced"
    row = dict(db.get_booking(booking_id))
    assert row["status"] == "Invoiced"
    return True


def test_invalid_status_rejected():
    booking_id = _create_booking()
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/status".format(booking_id),
        json={"status": "Pending"},
    )
    assert resp.status_code == 400
    return True


def test_dashboard_shows_updated_status_after_reload():
    customer = "Dashboard Status Reload {0}".format(os.getpid())
    booking_id = db.create_booking(
        customer,
        "0412000888",
        "reload@example.com",
        "1 Reload St, Perth WA",
        "2 Reload Ave, Fremantle WA",
        "2099-01-01",
        2,
        "reload test",
        status="Quote",
    )
    client = _login_client()
    client.post(
        "/bookings/{0}/status".format(booking_id),
        json={"status": "Completed"},
    )
    html = client.get("/dashboard?filter=completed&jobs_limit=500").get_data(as_text=True)
    assert customer in html
    idx = html.find('data-booking-id="{0}"'.format(booking_id))
    assert idx >= 0, "Expected booking on completed dashboard filter"
    window = html[idx : idx + 800]
    assert 'dashboard-status-label">Completed<' in window
    return True


def test_dashboard_rows_do_not_embed_status_menus():
    db.init_db()
    for idx in range(3):
        _create_booking(status="Confirmed", move_date="2098-01-{0:02d}".format(idx + 1))
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    assert html.count("dashboard-status-picker") >= 3
    assert html.count('class="dashboard-status-menu"') == 1
    assert html.count('class="dashboard-status-option"') == 0
    return True


def main():
    tests = [
        test_dashboard_renders_status_picker,
        test_dashboard_rows_do_not_embed_status_menus,
        test_inline_status_update_persists,
        test_invalid_status_rejected,
        test_dashboard_shows_updated_status_after_reload,
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
