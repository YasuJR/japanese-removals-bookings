#!/usr/bin/env python3
"""E2E tests — Payment Paid automatically sets Job Status to Completed."""

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
    label = "paid-completed-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Paid Completed Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _create_booking(status="Invoiced", payment_status="Unpaid"):
    global _test_user_counter
    _test_user_counter += 1
    return db.create_booking(
        "Paid Completed {0}-{1}".format(os.getpid(), _test_user_counter),
        "0412000111",
        "paid-completed-{0}@example.com".format(os.getpid()),
        "1 Paid St, Perth WA",
        "2 Paid Ave, Fremantle WA",
        "2026-10-01",
        2,
        "paid sets completed test",
        status=status,
        payment_status=payment_status,
    )


def test_dashboard_paid_sets_job_status_completed():
    booking_id = _create_booking(status="Invoiced", payment_status="Unpaid")
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/payment".format(booking_id),
        json={"payment_status": "Paid"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["payment_status"] == "Paid"
    assert payload["job_status"] == "Completed"
    assert payload["job_status_css"] == "completed"
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Completed"
    return True


def test_unpaid_part_paid_overdue_do_not_change_job_status():
    invoiced = _create_booking(status="Invoiced", payment_status="Unpaid")
    confirmed = _create_booking(status="Confirmed", payment_status="Unpaid")
    invoice.apply_payment_status(invoiced, invoice.PAYMENT_STATUS_UNPAID)
    invoice.apply_payment_status(confirmed, invoice.PAYMENT_STATUS_PART_PAID)
    overdue = _create_booking(status="Invoiced", payment_status="Unpaid")
    invoice.apply_payment_status(overdue, invoice.PAYMENT_STATUS_OVERDUE)
    assert dict(db.get_booking(invoiced))["status"] == "Invoiced"
    assert dict(db.get_booking(confirmed))["status"] == "Confirmed"
    assert dict(db.get_booking(overdue))["status"] == "Invoiced"
    assert dict(db.get_booking(invoiced))["payment_status"] == "Unpaid"
    assert dict(db.get_booking(confirmed))["payment_status"] == "Part Paid"
    assert dict(db.get_booking(overdue))["payment_status"] == "Overdue"
    return True


def test_reverting_paid_does_not_undo_completed():
    booking_id = _create_booking(status="Invoiced", payment_status="Unpaid")
    invoice.apply_payment_status(booking_id, invoice.PAYMENT_STATUS_PAID)
    assert dict(db.get_booking(booking_id))["status"] == "Completed"
    invoice.apply_payment_status(booking_id, invoice.PAYMENT_STATUS_UNPAID)
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Unpaid"
    assert row["status"] == "Completed"
    return True


def test_cancelled_paid_booking_stays_cancelled():
    booking_id = _create_booking(status="Cancelled", payment_status="Unpaid")
    invoice.apply_payment_status(booking_id, invoice.PAYMENT_STATUS_PAID)
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Cancelled"
    return True


def test_already_completed_stays_completed():
    booking_id = _create_booking(status="Completed", payment_status="Unpaid")
    invoice.apply_payment_status(booking_id, invoice.PAYMENT_STATUS_PAID)
    assert dict(db.get_booking(booking_id))["status"] == "Completed"
    return True


def test_existing_paid_invoiced_rows_are_backfilled():
    invoiced_paid = _create_booking(status="Invoiced", payment_status="Paid")
    confirmed_paid = _create_booking(status="Confirmed", payment_status="Paid")
    cancelled_paid = _create_booking(status="Cancelled", payment_status="Paid")
    unpaid_invoiced = _create_booking(status="Invoiced", payment_status="Unpaid")
    assert dict(db.get_booking(invoiced_paid))["status"] == "Invoiced"
    updated = db.complete_existing_paid_jobs()
    assert updated >= 2
    assert dict(db.get_booking(invoiced_paid))["status"] == "Completed"
    assert dict(db.get_booking(confirmed_paid))["status"] == "Completed"
    assert dict(db.get_booking(cancelled_paid))["status"] == "Cancelled"
    assert dict(db.get_booking(unpaid_invoiced))["status"] == "Invoiced"
    assert dict(db.get_booking(unpaid_invoiced))["payment_status"] == "Unpaid"
    return True


def test_dashboard_js_updates_status_from_payment_response():
    js = (ROOT / "static" / "dashboard_payment.js").read_text()
    status_js = (ROOT / "static" / "dashboard_status.js").read_text()
    assert "data.job_status" in js
    assert "dashboardApplyStatus" in js
    assert "window.dashboardApplyStatus" in status_js
    return True


def main():
    tests = [
        test_dashboard_paid_sets_job_status_completed,
        test_unpaid_part_paid_overdue_do_not_change_job_status,
        test_reverting_paid_does_not_undo_completed,
        test_cancelled_paid_booking_stays_cancelled,
        test_already_completed_stays_completed,
        test_existing_paid_invoiced_rows_are_backfilled,
        test_dashboard_js_updates_status_from_payment_response,
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
