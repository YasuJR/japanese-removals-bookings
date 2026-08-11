#!/usr/bin/env python3
"""Tests for automatic hourly rate and callout pricing based on number of movers."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth
import database as db
import invoice
import mover_pricing
from app import app
from validators import parse_booking_form

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")


def _form_dict(**overrides):
    base = {
        "customer_name": "Mover Pricing Customer",
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
        "status": "Quote",
    }
    base.update(overrides)
    return base


def test_two_movers_hourly_rate_is_180():
    assert mover_pricing.hourly_rate_for_movers(2) == 180.0
    state = mover_pricing.MoverPricingState()
    assert state.apply_hourly_for_movers(2, 0.0) == 180.0
    return True


def test_two_movers_callout_fee_is_90():
    assert mover_pricing.callout_fee_for_movers(2) == 90.0
    state = mover_pricing.MoverPricingState()
    assert state.apply_callout_for_movers(2, 0.0) == 90.0
    return True


def test_three_movers_hourly_rate_is_235():
    assert mover_pricing.hourly_rate_for_movers(3) == 235.0
    state = mover_pricing.MoverPricingState()
    assert state.apply_hourly_for_movers(3, 0.0) == 235.0
    return True


def test_three_movers_callout_fee_is_117_50():
    assert mover_pricing.callout_fee_for_movers(3) == 117.50
    state = mover_pricing.MoverPricingState()
    assert state.apply_callout_for_movers(3, 0.0) == 117.50
    return True


def test_switching_between_2_and_3_updates_both_values():
    state = mover_pricing.MoverPricingState()
    assert mover_pricing.pricing_for_movers(2) == (180.0, 90.0)
    assert mover_pricing.pricing_for_movers(3) == (235.0, 117.50)
    assert state.apply_hourly_for_movers(2, 0.0) == 180.0
    assert state.apply_callout_for_movers(2, 0.0) == 90.0
    assert state.apply_hourly_for_movers(3, 0.0) == 235.0
    assert state.apply_callout_for_movers(3, 0.0) == 117.50
    assert state.apply_hourly_for_movers(2, 0.0) == 180.0
    assert state.apply_callout_for_movers(2, 0.0) == 90.0
    return True


def test_manual_hourly_override_preserves_custom_hourly_rate():
    state = mover_pricing.MoverPricingState()
    assert state.apply_hourly_for_movers(2, 0.0) == 180.0
    state.mark_manual_hourly_override()
    assert state.apply_hourly_for_movers(3, 200.0) == 200.0
    assert state.apply_callout_for_movers(3, 0.0) == 117.50
    return True


def test_manual_callout_override_preserves_custom_callout_fee():
    state = mover_pricing.MoverPricingState()
    assert state.apply_callout_for_movers(2, 0.0) == 90.0
    state.mark_manual_callout_override()
    assert state.apply_callout_for_movers(3, 100.0) == 100.0
    assert state.apply_hourly_for_movers(3, 0.0) == 235.0
    return True


def test_invoice_total_recalculates_for_three_movers():
    data, errors = parse_booking_form(
        _form_dict(
            num_movers="3",
            hourly_rate="235",
            duration_hours="2",
            callout_fee="117.50",
        )
    )
    assert not errors
    totals = invoice.calculate_from_form_data(data)
    assert totals["hourly_rate"] == 235.0
    assert totals["callout_fee"] == 117.50
    assert totals["total"] == 587.50  # (235 * 2) + 117.50
    return True


def test_preview_calculate_endpoint_returns_updated_total_for_three_movers():
    db.init_db()
    uid = db.create_staff_user(
        "mover-pricing-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Mover Pricing Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "mover-pricing"

    resp = client.post(
        "/bookings/invoice/preview-calculate",
        data=_form_dict(
            num_movers="3",
            hourly_rate="235",
            callout_fee="117.50",
            duration_hours="1",
        ),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["hourly_rate"] == 235.0
    assert payload["callout_fee"] == 117.50
    assert payload["total"] == 352.50
    return True


def test_preview_calculate_endpoint_returns_updated_total_for_two_movers():
    db.init_db()
    uid = db.create_staff_user(
        "mover-pricing-two-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Mover Pricing Two",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "mover-pricing-two"

    resp = client.post(
        "/bookings/invoice/preview-calculate",
        data=_form_dict(
            num_movers="2",
            hourly_rate="180",
            callout_fee="90",
            duration_hours="1",
        ),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["hourly_rate"] == 180.0
    assert payload["callout_fee"] == 90.0
    assert payload["total"] == 270.0
    return True


def test_new_booking_page_includes_mover_pricing_scripts():
    db.init_db()
    uid = db.create_staff_user(
        "mover-pricing-page-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Mover Pricing Page",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "mover-pricing-page"

    resp = client.get("/bookings/new")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'name="num_movers"' in html
    assert 'name="callout_fee"' in html
    assert 'id="hourly_rate"' in html
    assert "mover_pricing.js" in html
    assert "117.5" in (ROOT / "static" / "mover_pricing.js").read_text()
    assert "Net sales (ex GST)" not in html
    assert "Invoice overrides" not in html
    assert "GST inclusive pricing" not in html
    assert "Invoice status" not in html
    assert "Payment status" not in html
    assert 'id="new-booking-invoice-panel"' not in html
    assert "new_booking_pricing.js" not in html
    return True


def test_new_booking_page_simplified_layout():
    db.init_db()
    uid = db.create_staff_user(
        "new-booking-ui-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "New Booking UI",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "new-booking-ui"

    html = client.get("/bookings/new").get_data(as_text=True)
    assert "Paste customer information" in html or "Analyse" in html
    assert "Extra charges" in html or "extra" in html.lower()
    assert "Hourly rate" in html
    assert "Callout fee" in html
    assert "Save booking" in html or "Confirm Booking" in html
    assert "invoice_bank_account_name" not in html
    assert "invoice_custom_text" not in html
    return True


def test_edit_booking_page_includes_mover_pricing_script():
    db.init_db()
    booking_id = db.create_booking(
        "Historical Customer",
        "0412345678",
        "historical@example.com",
        "1 Old St, Perth WA",
        "2 Old Ave, Fremantle WA",
        "2026-07-01",
        2,
        "Keep stored hourly rate",
        hourly_rate=150.0,
        callout_fee=90.0,
        gst_enabled=1,
        duration_hours="1",
    )
    uid = db.create_staff_user(
        "mover-pricing-edit-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Mover Pricing Edit",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "mover-pricing-edit"

    resp = client.get("/bookings/{0}/edit".format(booking_id))
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "mover_pricing.js" in html
    assert 'id="pricing_callout_fee"' in html
    assert 'value="150' in html or 'value="150.0"' in html
    row = db.get_booking(booking_id)
    assert float(row["hourly_rate"]) == 150.0
    assert float(row["callout_fee"]) == 90.0
    return True


def main():
    tests = [
        test_two_movers_hourly_rate_is_180,
        test_two_movers_callout_fee_is_90,
        test_three_movers_hourly_rate_is_235,
        test_three_movers_callout_fee_is_117_50,
        test_switching_between_2_and_3_updates_both_values,
        test_manual_hourly_override_preserves_custom_hourly_rate,
        test_manual_callout_override_preserves_custom_callout_fee,
        test_invoice_total_recalculates_for_three_movers,
        test_preview_calculate_endpoint_returns_updated_total_for_three_movers,
        test_preview_calculate_endpoint_returns_updated_total_for_two_movers,
        test_new_booking_page_includes_mover_pricing_scripts,
        test_new_booking_page_simplified_layout,
        test_edit_booking_page_includes_mover_pricing_script,
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
