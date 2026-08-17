#!/usr/bin/env python3
"""E2E tests — sync Xero paid invoices to booking payment_status."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import invoice
from app import app
from integrations import xero_payment_sync


_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    uid = db.create_staff_user(
        "xero-pay-sync-{0}-{1}".format(os.getpid(), _test_user_counter),
        auth.hash_password("test"),
        "Xero Payment Sync Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _sample_xero_invoice(
    invoice_number="22",
    *,
    total=720.0,
    amount_paid=720.0,
    amount_due=0.0,
    invoice_id="xero-inv-22",
):
    return {
        "InvoiceID": invoice_id,
        "InvoiceNumber": invoice_number,
        "Total": total,
        "AmountPaid": amount_paid,
        "AmountDue": amount_due,
        "Status": "PAID",
        "FullyPaidOnDate": "/Date(1715760000000)/",
    }


def _create_booking(customer_name, invoice_number="", booking_id_hint=None):
    move_date = "2026-11-01"
    booking_id = db.create_booking(
        customer_name,
        "0412000333",
        "xero-sync@example.com",
        "1 Sync St, Perth WA",
        "2 Sync Ave, Fremantle WA",
        move_date,
        2,
        "xero payment sync test",
        status="Invoiced",
    )
    fields = {}
    if invoice_number:
        fields["invoice_number"] = invoice_number
    if fields:
        db.update_booking_invoice_fields(booking_id, fields)
    return booking_id


def test_fully_paid_xero_invoice_marks_booking_paid():
    booking_id = _create_booking("Xero Sync Full Pay", invoice_number="22")
    booking = dict(db.get_booking(booking_id))
    inv = _sample_xero_invoice("INV22")

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        return_value=(inv, None),
    ), patch(
        "integrations.xero.persist_invoice_from_xero"
    ) as persist_mock:
        outcome = xero_payment_sync.sync_booking_payment_from_xero(booking)

    assert outcome["ok"] is True
    assert outcome["updated"] is True
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["paid_at"]
    persist_mock.assert_called_once()
    return True


def test_partial_payment_does_not_mark_paid():
    booking_id = _create_booking("Xero Sync Partial Pay", invoice_number="22")
    booking = dict(db.get_booking(booking_id))
    inv = _sample_xero_invoice("22", total=720.0, amount_paid=500.0, amount_due=220.0)

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        return_value=(inv, None),
    ):
        outcome = xero_payment_sync.sync_booking_payment_from_xero(booking)

    assert outcome["updated"] is False
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] != "Paid"
    return True


def test_missing_xero_invoice_does_not_update_other_bookings():
    booking_a = _create_booking("Xero Sync Missing A", invoice_number="901")
    booking_b = _create_booking("Xero Sync Missing B", invoice_number="902")

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        return_value=(None, None),
    ):
        result = xero_payment_sync.sync_xero_payments()

    assert result["ok"] is True
    assert result["updated"] == 0
    assert dict(db.get_booking(booking_a))["payment_status"] != "Paid"
    assert dict(db.get_booking(booking_b))["payment_status"] != "Paid"
    return True


def test_same_customer_different_invoice_numbers_only_match_correct_booking():
    shared_customer = "Shared Customer Xero Sync"
    booking_22 = _create_booking(shared_customer, invoice_number="22")
    booking_23 = _create_booking(shared_customer, invoice_number="23")
    inv_22 = _sample_xero_invoice("INV22", invoice_id="xero-inv-22-only")

    def fetch_side_effect(booking):
        if booking["id"] == booking_22:
            return inv_22, None
        return None, None

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=fetch_side_effect,
    ), patch("integrations.xero.persist_invoice_from_xero"):
        result = xero_payment_sync.sync_xero_payments()

    assert result["updated"] == 1
    assert dict(db.get_booking(booking_22))["payment_status"] == "Paid"
    assert dict(db.get_booking(booking_23))["payment_status"] != "Paid"
    return True


def test_xero_api_error_leaves_bookings_unchanged():
    booking_id = _create_booking("Xero Sync API Error", invoice_number="22")
    before = dict(db.get_booking(booking_id))

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=RuntimeError("Xero API unavailable"),
    ):
        result = xero_payment_sync.sync_xero_payments()

    after = dict(db.get_booking(booking_id))
    assert after["payment_status"] == before["payment_status"]
    assert result["ok"] is True
    assert len(result["errors"]) >= 1
    return True


def test_dashboard_renders_sync_button():
    client = _login_client()
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Sync Xero Payments" in html
    assert 'name="action" value="sync_xero_payments"' in html
    return True


def test_dashboard_sync_action_updates_paid_booking():
    booking_id = _create_booking("Dashboard Xero Sync Paid", invoice_number="22")
    inv = _sample_xero_invoice("22")

    client = _login_client()
    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        return_value=(inv, None),
    ), patch("integrations.xero.persist_invoice_from_xero"):
        resp = client.post(
            "/dashboard?filter=all",
            data={"action": "sync_xero_payments"},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    html = resp.get_data(as_text=True)
    assert "Synced 1 payment" in html or "marked Paid" in html or "Synced" in html
    return True


def test_manual_paid_not_reverted_when_xero_unpaid():
    booking_id = _create_booking("Manual Paid Stays", invoice_number="22")
    invoice.apply_payment_status(booking_id, invoice.PAYMENT_STATUS_PAID)
    booking = dict(db.get_booking(booking_id))
    inv = _sample_xero_invoice("22", total=720.0, amount_paid=0.0, amount_due=720.0)

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        return_value=(inv, None),
    ):
        outcome = xero_payment_sync.sync_booking_payment_from_xero(booking)

    assert outcome["skipped"] is True
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    return True


def main():
    tests = [
        test_fully_paid_xero_invoice_marks_booking_paid,
        test_partial_payment_does_not_mark_paid,
        test_missing_xero_invoice_does_not_update_other_bookings,
        test_same_customer_different_invoice_numbers_only_match_correct_booking,
        test_xero_api_error_leaves_bookings_unchanged,
        test_dashboard_renders_sync_button,
        test_dashboard_sync_action_updates_paid_booking,
        test_manual_paid_not_reverted_when_xero_unpaid,
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
