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
_invoice_counter = 80000 + os.getpid() % 1000


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


def _next_invoice_number():
    global _invoice_counter
    _invoice_counter += 1
    return str(_invoice_counter)


def _sample_xero_invoice(
    invoice_number="22",
    *,
    total=720.0,
    amount_paid=720.0,
    amount_due=0.0,
    invoice_id="xero-inv-22",
    status="PAID",
):
    return {
        "InvoiceID": invoice_id,
        "InvoiceNumber": invoice_number,
        "Total": total,
        "AmountPaid": amount_paid,
        "AmountDue": amount_due,
        "Status": status,
        "FullyPaidOnDate": "/Date(1715760000000)/",
    }


def _fetch_matching(inv):
    """Return the sample invoice only when the booking's stored number matches."""

    def fetch(booking, **_kwargs):
        if xero_payment_sync.invoice_numbers_match(booking, inv):
            return inv, None
        return None, None

    return fetch


def _create_booking(customer_name, invoice_number="", status="Invoiced"):
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
        status=status,
    )
    if invoice_number:
        db.update_booking_invoice_fields(booking_id, {"invoice_number": invoice_number})
    return booking_id


def test_fully_paid_xero_invoice_marks_booking_paid():
    number = _next_invoice_number()
    booking_id = _create_booking("Xero Sync Full Pay", invoice_number=number)
    booking = dict(db.get_booking(booking_id))
    inv = _sample_xero_invoice("INV{0}".format(number), invoice_id="xero-inv-{0}".format(number))

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ), patch("integrations.xero.persist_invoice_from_xero") as persist_mock:
        outcome = xero_payment_sync.sync_booking_payment_from_xero(booking)

    assert outcome["ok"] is True
    assert outcome["updated"] is True
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["paid_at"]
    assert row["status"] == "Completed"
    persist_mock.assert_not_called()
    lines = "\n".join(outcome.get("log_lines") or [])
    assert "invoice=INV{0}".format(number) in lines
    assert "booking_id={0}".format(booking_id) in lines
    assert "previous_payment_status=Unpaid" in lines
    assert "new_payment_status=Paid" in lines
    assert "sync_timestamp=" in lines
    return True


def test_partial_payment_does_not_mark_paid():
    number = _next_invoice_number()
    booking_id = _create_booking("Xero Sync Partial Pay", invoice_number=number)
    booking = dict(db.get_booking(booking_id))
    inv = _sample_xero_invoice(
        "INV{0}".format(number),
        total=720.0,
        amount_paid=500.0,
        amount_due=220.0,
        status="AUTHORISED",
        invoice_id="xero-inv-{0}".format(number),
    )

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ):
        outcome = xero_payment_sync.sync_booking_payment_from_xero(booking)

    assert outcome["updated"] is False
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] != "Paid"
    assert row["status"] == "Invoiced"
    return True


def test_authorised_zero_due_is_treated_as_fully_paid():
    number = _next_invoice_number()
    booking_id = _create_booking("Xero Sync Authorised Paid", invoice_number=number)
    booking = dict(db.get_booking(booking_id))
    inv = _sample_xero_invoice(
        "INV{0}".format(number),
        status="AUTHORISED",
        invoice_id="xero-inv-{0}".format(number),
    )

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ):
        outcome = xero_payment_sync.sync_booking_payment_from_xero(booking)

    assert outcome["updated"] is True
    assert dict(db.get_booking(booking_id))["payment_status"] == "Paid"
    assert dict(db.get_booking(booking_id))["status"] == "Completed"
    return True


def test_voided_invoice_is_not_marked_paid():
    number = _next_invoice_number()
    booking_id = _create_booking("Xero Sync Voided", invoice_number=number)
    booking = dict(db.get_booking(booking_id))
    inv = _sample_xero_invoice(
        "INV{0}".format(number),
        total=720.0,
        amount_paid=720.0,
        amount_due=0.0,
        status="VOIDED",
        invoice_id="xero-inv-{0}".format(number),
    )

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ):
        outcome = xero_payment_sync.sync_booking_payment_from_xero(booking)

    assert outcome["updated"] is False
    assert dict(db.get_booking(booking_id))["payment_status"] != "Paid"
    return True


def test_booking_id_is_not_used_as_invoice_number():
    booking_id = _create_booking("Xero Sync No Invoice Number")
    booking = dict(db.get_booking(booking_id))
    assert xero_payment_sync.stored_invoice_reference_number(booking) is None
    assert xero_payment_sync._booking_eligible_for_sync(booking) is False
    fake_inv = _sample_xero_invoice("INV{0}".format(booking_id), invoice_id="xero-inv-id-fallback")
    assert xero_payment_sync.invoice_numbers_match(booking, fake_inv) is False
    return True


def test_duplicate_invoice_numbers_are_unmatched():
    number = _next_invoice_number()
    first = _create_booking("Xero Sync Dup A", invoice_number=number)
    second = _create_booking("Xero Sync Dup B", invoice_number=number)
    inv = _sample_xero_invoice("INV{0}".format(number), invoice_id="xero-inv-{0}".format(number))

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ):
        result = xero_payment_sync.sync_xero_payments()

    assert result["updated"] == 0
    assert result["unmatched"] >= 1
    lines = "\n".join(result.get("log_lines") or [])
    assert "unmatched invoice=INV{0}".format(number) in lines
    assert "multiple bookings" in lines
    assert dict(db.get_booking(first))["payment_status"] != "Paid"
    assert dict(db.get_booking(second))["payment_status"] != "Paid"
    return True


def test_missing_xero_invoice_does_not_update_other_bookings():
    booking_a = _create_booking("Xero Sync Missing A", invoice_number=_next_invoice_number())
    booking_b = _create_booking("Xero Sync Missing B", invoice_number=_next_invoice_number())

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
    number_22 = _next_invoice_number()
    number_23 = _next_invoice_number()
    booking_22 = _create_booking(shared_customer, invoice_number=number_22)
    booking_23 = _create_booking(shared_customer, invoice_number=number_23)
    inv_22 = _sample_xero_invoice(
        "INV{0}".format(number_22), invoice_id="xero-inv-{0}-only".format(number_22)
    )

    def fetch_side_effect(booking, **_kwargs):
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
    assert dict(db.get_booking(booking_22))["status"] == "Completed"
    assert dict(db.get_booking(booking_23))["status"] == "Invoiced"
    return True


def test_xero_api_error_leaves_bookings_unchanged():
    number = _next_invoice_number()
    booking_id = _create_booking("Xero Sync API Error", invoice_number=number)
    before = dict(db.get_booking(booking_id))

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=RuntimeError("Xero API unavailable"),
    ):
        result = xero_payment_sync.sync_xero_payments()

    after = dict(db.get_booking(booking_id))
    assert after["payment_status"] == before["payment_status"]
    assert after["status"] == before["status"]
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
    number = _next_invoice_number()
    booking_id = _create_booking("Dashboard Xero Sync Paid", invoice_number=number)
    inv = _sample_xero_invoice("INV{0}".format(number), invoice_id="xero-inv-{0}".format(number))

    client = _login_client()
    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ), patch("integrations.xero.persist_invoice_from_xero"):
        resp = client.post(
            "/dashboard?filter=all",
            data={"action": "sync_xero_payments"},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Completed"
    html = resp.get_data(as_text=True)
    assert "Synced 1 payment" in html or "marked Paid" in html or "Synced" in html
    return True


def test_manual_paid_not_reverted_when_xero_unpaid():
    number = _next_invoice_number()
    booking_id = _create_booking("Manual Paid Stays", invoice_number=number)
    invoice.apply_payment_status(booking_id, invoice.PAYMENT_STATUS_PAID)
    booking = dict(db.get_booking(booking_id))
    inv = _sample_xero_invoice(
        "INV{0}".format(number),
        total=720.0,
        amount_paid=0.0,
        amount_due=720.0,
        status="AUTHORISED",
        invoice_id="xero-inv-{0}".format(number),
    )

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ):
        outcome = xero_payment_sync.sync_booking_payment_from_xero(booking)

    assert outcome["skipped"] is True
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    return True


def test_cron_entrypoint_uses_same_sync():
    number = _next_invoice_number()
    booking_id = _create_booking("Cron Xero Sync Paid", invoice_number=number)
    inv = _sample_xero_invoice("INV{0}".format(number), invoice_id="xero-inv-{0}".format(number))

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ), patch("integrations.xero.persist_invoice_from_xero"):
        import scripts.run_xero_payment_sync as cron_script

        code = cron_script.main([])

    assert code == 0
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Completed"
    state = xero_payment_sync.load_sync_state()
    assert state.get("last_source") == "cron"
    assert state.get("last_success_at")
    return True


def test_repeat_sync_is_idempotent():
    number = _next_invoice_number()
    booking_id = _create_booking("Repeat Sync Paid", invoice_number=number)
    inv = _sample_xero_invoice("INV{0}".format(number), invoice_id="xero-inv-{0}".format(number))

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ), patch("integrations.xero.persist_invoice_from_xero"):
        first = xero_payment_sync.sync_xero_payments(source="manual")
        second = xero_payment_sync.sync_xero_payments(source="manual")

    assert first["updated"] == 1
    assert second["updated"] == 0
    assert dict(db.get_booking(booking_id))["payment_status"] == "Paid"
    return True


def test_dry_run_does_not_update_payment():
    number = _next_invoice_number()
    booking_id = _create_booking("Dry Run Sync", invoice_number=number)
    inv = _sample_xero_invoice("INV{0}".format(number), invoice_id="xero-inv-{0}".format(number))

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ):
        result = xero_payment_sync.sync_xero_payments(source="cron", dry_run=True)

    assert result["updated"] == 0
    assert dict(db.get_booking(booking_id))["payment_status"] != "Paid"
    lines = "\n".join(result.get("log_lines") or [])
    assert "dry-run=true" in lines
    assert "new_payment_status=Paid" in lines
    return True


def test_sync_logs_include_expected_lines():
    number = _next_invoice_number()
    booking_id = _create_booking("Sync Logs Paid", invoice_number=number)
    inv = _sample_xero_invoice("INV{0}".format(number), invoice_id="xero-inv-{0}".format(number))

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ), patch("integrations.xero.persist_invoice_from_xero"):
        result = xero_payment_sync.sync_xero_payments(source="manual")

    lines = "\n".join(result.get("log_lines") or [])
    assert result["updated"] == 1
    assert "Xero payment sync started" in lines
    assert "matched invoice=INV{0}".format(number) in lines
    assert "booking_id={0}".format(booking_id) in lines
    assert "previous_payment_status=Unpaid" in lines
    assert "new_payment_status=Paid" in lines
    assert "sync_timestamp=" in lines
    assert "1 booking(s) updated" in lines
    assert "Xero payment sync completed" in lines
    assert dict(db.get_booking(booking_id))["payment_status"] == "Paid"
    return True


def test_dashboard_shows_last_sync_after_manual_sync():
    number = _next_invoice_number()
    booking_id = _create_booking("Dashboard Last Sync", invoice_number=number)
    inv = _sample_xero_invoice("INV{0}".format(number), invoice_id="xero-inv-{0}".format(number))
    client = _login_client()

    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero_payment_sync.fetch_xero_invoice_for_booking",
        side_effect=_fetch_matching(inv),
    ), patch("integrations.xero.persist_invoice_from_xero"):
        client.post(
            "/dashboard?filter=all",
            data={"action": "sync_xero_payments"},
            follow_redirects=True,
        )

    html = client.get("/dashboard?filter=all").get_data(as_text=True)
    assert "Last Xero Sync:" in html
    assert dict(db.get_booking(booking_id))["payment_status"] == "Paid"
    return True


def test_fully_paid_helper_rejects_partial_and_voided():
    paid = _sample_xero_invoice("INV31", status="PAID")
    partial = _sample_xero_invoice(
        "INV31", total=720.0, amount_paid=100.0, amount_due=620.0, status="AUTHORISED"
    )
    voided = _sample_xero_invoice("INV31", status="VOIDED")
    assert xero_payment_sync.is_xero_invoice_fully_paid(paid) is True
    assert xero_payment_sync.is_xero_invoice_fully_paid(partial) is False
    assert xero_payment_sync.is_xero_invoice_fully_paid(voided) is False
    assert xero_payment_sync.invoice_numbers_match(
        {"invoice_number": "31"}, {"InvoiceNumber": "INV31"}
    )
    assert xero_payment_sync.invoice_numbers_match(
        {"invoice_number": "INV-31"}, {"InvoiceNumber": "INV31"}
    )
    return True


def main():
    tests = [
        test_fully_paid_xero_invoice_marks_booking_paid,
        test_partial_payment_does_not_mark_paid,
        test_authorised_zero_due_is_treated_as_fully_paid,
        test_voided_invoice_is_not_marked_paid,
        test_booking_id_is_not_used_as_invoice_number,
        test_duplicate_invoice_numbers_are_unmatched,
        test_missing_xero_invoice_does_not_update_other_bookings,
        test_same_customer_different_invoice_numbers_only_match_correct_booking,
        test_xero_api_error_leaves_bookings_unchanged,
        test_dashboard_renders_sync_button,
        test_dashboard_sync_action_updates_paid_booking,
        test_manual_paid_not_reverted_when_xero_unpaid,
        test_cron_entrypoint_uses_same_sync,
        test_repeat_sync_is_idempotent,
        test_dry_run_does_not_update_payment,
        test_sync_logs_include_expected_lines,
        test_dashboard_shows_last_sync_after_manual_sync,
        test_fully_paid_helper_rejects_partial_and_voided,
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
