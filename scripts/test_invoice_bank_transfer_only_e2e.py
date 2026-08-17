#!/usr/bin/env python3
"""E2E tests — customer invoices show bank transfer only (card/Stripe hidden)."""

import re
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth
import config
import database as db
import invoice
from app import app
from integrations import invoice_pdf, stripe as stripe_service
from integrations import stripe_config


_test_client_counter = 0


def _login_client():
    global _test_client_counter
    _test_client_counter += 1
    db.init_db()
    label = "bank-only-{0}-{1}".format(__import__("os").getpid(), _test_client_counter)
    uid = db.create_staff_user(
        label,
        auth.hash_password("test"),
        "Bank Only Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_unpaid_booking():
    booking_id = db.create_booking(
        "Bank Transfer Test",
        "0412000111",
        "bank-test@example.com",
        "1 Test St, Perth WA",
        "2 Test Ave, Fremantle WA",
        "2026-09-01",
        2,
        "Bank transfer only test",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        duration_hours="1",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
    )
    db.update_booking_invoice_fields(
        booking_id,
        {
            "invoice_number": "INV-BANK-{0}".format(booking_id),
            "invoice_status": "AUTHORISED",
        },
    )
    db.ensure_payment_token(booking_id)
    return booking_id


def test_card_payments_disabled_by_default():
    assert config.INVOICE_CARD_PAYMENTS_ENABLED is False
    assert stripe_config.invoice_card_payments_enabled() is False
    return True


def test_payment_options_hide_card_checkout():
    booking_id = _create_unpaid_booking()
    booking = dict(db.get_booking(booking_id))
    options = stripe_service.payment_options_for_booking(booking)
    assert options["card_payments_visible"] is False
    assert options["can_checkout"] is False
    assert options["can_pay_now"] is False
    assert not options["pay_now_url"]
    assert options["bank_total_display"]
    return True


def test_invoice_preview_hides_stripe():
    booking_id = _create_unpaid_booking()
    client = _login_client()
    html = client.get("/bookings/{0}/invoice/preview".format(booking_id)).get_data(as_text=True)
    assert "Bank Transfer" in html
    assert "Account Name" in html or "Account name" in html.lower()
    assert "BSB" in html
    assert "Payment Reference" in html
    assert "INV-BANK-" in html
    assert "Pay Now" not in html
    assert "Credit Card" not in html
    assert "stripe_checkout" not in html
    return True


def test_invoice_pdf_hides_stripe():
    booking_id = _create_unpaid_booking()
    booking = dict(db.get_booking(booking_id))
    doc = invoice_pdf.build_invoice_document(booking)
    options = doc.get("payment_options") or {}
    assert options.get("card_payments_visible") is False
    assert options.get("can_pay_now") is False
    pdf_bytes = invoice_pdf.generate_invoice_pdf(booking)
    assert len(pdf_bytes) > 1000
    return True


def test_public_pay_link_shows_bank_transfer():
    booking_id = _create_unpaid_booking()
    token = db.ensure_payment_token(booking_id)
    client = app.test_client()
    resp = client.get("/pay/{0}".format(token))
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Pay by bank transfer" in html
    assert "Account Name" in html or "account name" in html.lower()
    assert "BSB" in html
    assert "Payment Reference" in html
    assert "INV-BANK-" in html
    assert "checkout.stripe.com" not in html
    assert resp.headers.get("Location", "") == ""
    return True


def test_stripe_checkout_blocked_when_disabled():
    booking_id = _create_unpaid_booking()
    ok, msg, url = stripe_service.create_checkout_session(
        dict(db.get_booking(booking_id)),
        success_url="http://localhost/success",
        cancel_url="http://localhost/cancel",
    )
    assert not ok
    assert not url
    assert "bank transfer" in msg.lower()
    row = dict(db.get_booking(booking_id))
    assert row.get("payment_status") == invoice.PAYMENT_STATUS_UNPAID
    return True


def test_invoice_send_email_omits_pay_link():
    booking_id = _create_unpaid_booking()
    captured = {}

    def _capture_email(to, subject, body, pdf_bytes, filename):
        captured["body"] = body
        return True, "sent"

    with patch("integrations.email_send.is_configured", return_value=True), patch(
        "integrations.email_send.send_email_with_attachment", side_effect=_capture_email
    ), patch("integrations.invoice_pdf.generate_invoice_pdf", return_value=b"%PDF-test"):
        import services

        ok, msg = services.send_customer_invoice(booking_id)
    assert ok, msg
    body = captured.get("body", "")
    assert "Pay by credit card" not in body
    assert "Pay now:" not in body
    assert "Bank transfer details are on the attached PDF" in body
    row = dict(db.get_booking(booking_id))
    assert row.get("payment_status") == invoice.PAYMENT_STATUS_UNPAID
    return True


def main():
    tests = [
        test_card_payments_disabled_by_default,
        test_payment_options_hide_card_checkout,
        test_invoice_preview_hides_stripe,
        test_invoice_pdf_hides_stripe,
        test_public_pay_link_shows_bank_transfer,
        test_stripe_checkout_blocked_when_disabled,
        test_invoice_send_email_omits_pay_link,
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
