#!/usr/bin/env python3
"""E2E tests — Edit Booking invoice pricing, update, and send workflow."""

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import automation
import database as db
import invoice
import services
from integrations import invoice_send, stripe as stripe_service
from validators import parse_booking_form


def _mock_email(to, subject, body, pdf_bytes, filename):
    return True, "Email sent to {0}.".format(to)


def _mock_sms(booking, body, **kwargs):
    return True, "SMS sent.", "SMtest_invoice"


def _create_booking(email="", phone="0412345678", **kwargs):
    defaults = invoice.default_invoice_fields()
    booking_id = db.create_booking(
        kwargs.get("customer_name", "Invoice Test Customer"),
        phone,
        email,
        "1 Test St, Perth WA",
        "2 Test Ave, Fremantle WA",
        "2026-08-08",
        2,
        "Invoice workflow test",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        duration_hours="1",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
    )
    db.update_booking_invoice_fields(
        booking_id,
        {
            "invoice_number": "INV-TEST-{0}".format(booking_id),
            "invoice_status": "DRAFT",
        },
    )
    db.ensure_payment_token(booking_id)
    return booking_id


def test_email_wins_when_both_present():
    booking_id = _create_booking(email="customer@example.com", phone="0412987654")
    dest = invoice_send.resolve_send_destination(
        services.booking_to_dict(db.get_booking(booking_id))
    )
    assert dest["can_send"] and dest["method"] == "email", dest
    assert dest["destination"] == "customer@example.com"
    return {"name": "email_priority", "ok": True}


def test_email_send_only():
    booking_id = _create_booking(email="customer@example.com")
    with patch("integrations.email_send.is_configured", return_value=True), patch(
        "integrations.email_send.send_email_with_attachment", side_effect=_mock_email
    ), patch("integrations.invoice_pdf.generate_invoice_pdf", return_value=b"%PDF-test"):
        ok, msg = services.send_customer_invoice(booking_id)
    assert ok, msg
    row = dict(db.get_booking(booking_id))
    assert row.get("invoice_sent_method") == "email"
    assert row.get("invoice_sent_to") == "customer@example.com"
    logs = [
        e
        for e in db.list_automation_logs(limit=20)
        if e.get("booking_id") == booking_id
        and e.get("automation_type") == automation.AUTOMATION_INVOICE_SEND
    ]
    assert logs, "Expected invoice send log entry"
    return {"name": "email_send", "ok": True}


def test_sms_when_no_email():
    booking_id = _create_booking(email="", phone="0412987654")
    with patch("integrations.sms.is_configured", return_value=True), patch(
        "integrations.sms.send_message", side_effect=_mock_sms
    ):
        ok, msg = services.send_customer_invoice(booking_id)
    assert ok, msg
    row = dict(db.get_booking(booking_id))
    assert row.get("invoice_sent_method") == "sms"
    return {"name": "sms_fallback", "ok": True}


def test_blocks_without_contact():
    booking_id = _create_booking(email="", phone="")
    dest = invoice_send.resolve_send_destination(services.booking_to_dict(db.get_booking(booking_id)))
    assert not dest["can_send"]
    assert "required" in dest["blocked_reason"].lower()
    ok, msg = services.send_customer_invoice(booking_id)
    assert not ok
    return {"name": "blocked_no_contact", "ok": True}


def test_hourly_rate_recalc():
    data, errors = parse_booking_form(
        _form_dict(hourly_rate="200", duration_hours="2", callout_fee="90")
    )
    assert not errors
    totals = invoice.calculate_from_form_data(data)
    assert totals["total"] == 490.0  # (200*2 + 90) GST inclusive
    return {"name": "hourly_recalc", "ok": True}


def test_callout_recalc():
    data, _ = parse_booking_form(
        _form_dict(hourly_rate="180", duration_hours="1", callout_fee="120")
    )
    totals = invoice.calculate_from_form_data(data)
    assert totals["total"] == 300.0
    return {"name": "callout_recalc", "ok": True}


def test_xero_sync_on_update():
    booking_id = _create_booking(email="xero@example.com")
    db.update_booking_integration_fields(
        booking_id, {"xero_invoice_id": "00000000-0000-0000-0000-000000000001"}
    )
    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero.is_real_invoice_id", return_value=True
    ), patch("integrations.xero.is_draft_invoice", return_value=True), patch(
        "integrations.xero.sync_invoice_record",
        return_value=(True, "Xero draft updated.", {}),
    ) as sync_mock:
        ok, errors, msg = services.update_booking_invoice(
            booking_id, _form_dict(hourly_rate="190", callout_fee="90")
        )
    assert ok and not errors, errors
    assert sync_mock.called
    return {"name": "xero_sync", "ok": True}


def test_pay_link_after_price_change():
    booking_id = _create_booking(email="pay@example.com")
    ok, errors, _msg = services.update_booking_invoice(
        booking_id, _form_dict(hourly_rate="220", callout_fee="90")
    )
    assert ok and not errors
    url = services.prepare_booking_payment_link(booking_id)
    assert url and "/pay/" in url
    booking = services.booking_to_dict(db.get_booking(booking_id))
    options = stripe_service.payment_options_for_booking(booking)
    assert options["bank_total"] == 310.0
    return {"name": "pay_link", "ok": True}


def _form_dict(**overrides):
    base = {
        "customer_name": "Invoice Test Customer",
        "phone": "0412345678",
        "email": "customer@example.com",
        "pickup_address": "1 Test St, Perth WA",
        "delivery_address": "2 Test Ave, Fremantle WA",
        "move_date": "2026-08-08",
        "num_movers": "2",
        "notes": "",
        "start_time": "08:00",
        "finish_time": "09:00",
        "duration_hours": "1",
        "hourly_rate": "180",
        "callout_fee": "90",
        "gst_enabled": "on",
        "payment_status": "Unpaid",
        "invoice_status": "DRAFT",
        "status": "Completed",
    }
    base.update(overrides)
    return base


class _FakeForm(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def main():
    db.init_db()
    tests = [
        test_email_wins_when_both_present,
        test_email_send_only,
        test_sms_when_no_email,
        test_blocks_without_contact,
        test_hourly_rate_recalc,
        test_callout_recalc,
        test_xero_sync_on_update,
        test_pay_link_after_price_change,
    ]
    results = []
    failed = 0
    for fn in tests:
        try:
            results.append(fn())
            print("PASS:", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", fn.__name__, exc)
            results.append({"name": fn.__name__, "ok": False, "error": str(exc)})
    print("\n{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
