#!/usr/bin/env python3
"""E2E tests — invoice duration from start/finish times (not stale stored duration)."""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import invoice
from app import app
from booking_times import duration_hours_from_times, effective_duration_hours
from integrations import invoice_pdf
from validators import parse_booking_form


def test_duration_hours_from_times_example():
    hours = duration_hours_from_times("08:15", "14:00")
    assert hours == 5.75, hours
    return True


def test_effective_duration_prefers_times_over_stored():
    booking = {
        "start_time": "08:15",
        "finish_time": "14:00",
        "duration_hours": "5",
    }
    assert effective_duration_hours(booking) == 5.75
    return True


def test_invoice_totals_use_start_finish_not_stored_duration():
    booking = {
        "start_time": "08:15",
        "finish_time": "14:00",
        "duration_hours": "5",
        "hourly_rate": 180.0,
        "callout_fee": 90.0,
        "gst_enabled": 1,
        "extra_charges": [],
    }
    totals = invoice.calculate_invoice_totals(booking)
    assert totals["hours"] == 5.75
    assert round(totals["hourly_rate"] * totals["hours"], 2) == 1035.0
    assert totals["labour_gross"] == 1125.0
    return True


def test_calculate_from_form_data_ignores_stale_duration():
    data, errors = parse_booking_form(
        {
            "customer_name": "Duration Test",
            "phone": "0412000444",
            "email": "duration@example.com",
            "pickup_address": "1 Test St, Perth WA",
            "delivery_address": "2 Test Ave, Fremantle WA",
            "move_date": "2026-09-21",
            "num_movers": "2",
            "notes": "",
            "start_time": "08:15",
            "finish_time": "14:00",
            "duration_hours": "5",
            "hourly_rate": "180",
            "callout_fee": "90",
            "gst_enabled": "on",
            "payment_status": "Unpaid",
            "status": "Completed",
        }
    )
    assert not errors, errors
    totals = invoice.calculate_from_form_data(data)
    assert totals["hours"] == 5.75
    assert round(totals["hourly_rate"] * totals["hours"], 2) == 1035.0
    assert totals["labour_gross"] == 1125.0
    return True


def test_preview_calculate_endpoint_matches():
    db.init_db()
    uid = db.create_staff_user(
        "invoice-duration-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Invoice Duration Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid

    resp = client.post(
        "/bookings/invoice/preview-calculate",
        data={
            "customer_name": "Duration Test",
            "phone": "0412000444",
            "email": "duration@example.com",
            "pickup_address": "1 Test St, Perth WA",
            "delivery_address": "2 Test Ave, Fremantle WA",
            "move_date": "2026-09-21",
            "num_movers": "2",
            "notes": "",
            "start_time": "08:15",
            "finish_time": "14:00",
            "duration_hours": "5",
            "hourly_rate": "180",
            "callout_fee": "90",
            "gst_enabled": "on",
            "payment_status": "Unpaid",
            "status": "Completed",
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["hours"] == 5.75
    assert payload["labour_gross"] == 1125.0
    return True


def test_invoice_preview_and_pdf_match():
    db.init_db()
    booking_id = db.create_booking(
        "Duration PDF Test",
        "0412000555",
        "pdf-duration@example.com",
        "1 PDF St, Perth WA",
        "2 PDF Ave, Fremantle WA",
        "2026-09-22",
        2,
        "duration pdf test",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        start_time="08:15",
        finish_time="14:00",
        duration_hours="5",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
    )
    row = dict(db.get_booking(booking_id))
    row["extra_charges"] = db.list_extra_charges(booking_id)

    doc = invoice_pdf.build_invoice_document(row)
    totals = doc["totals"]
    assert totals["hours"] == 5.75
    assert round(totals["hourly_rate"] * totals["hours"], 2) == 1035.0
    assert totals["labour_gross"] == 1125.0
    assert "5.75 hrs" in doc["line_items"][0]["description_html"]

    uid = db.create_staff_user(
        "invoice-duration-preview-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Invoice Duration Preview",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    html = client.get("/bookings/{0}/invoice/preview".format(booking_id)).get_data(
        as_text=True
    )
    assert "5.75 hrs" in html
    assert "$1,035.00" in html
    assert "$1,125.00" in html or invoice.format_aud(totals["total"]) in html
    return True


def test_edit_form_duration_matches_invoice():
    db.init_db()
    booking_id = db.create_booking(
        "Edit Duration Test",
        "0412000666",
        "edit-duration@example.com",
        "1 Edit St, Perth WA",
        "2 Edit Ave, Fremantle WA",
        "2026-09-23",
        2,
        "edit duration test",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        start_time="08:15",
        finish_time="14:00",
        duration_hours="5",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
    )
    uid = db.create_staff_user(
        "invoice-duration-edit-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Invoice Duration Edit",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert re.search(
        r'id="pricing_duration_hours"[^>]+value="5\.75"',
        html,
    ), "Expected pricing duration input to show 5.75"
    assert "$1,035.00" in html or "1035" in html
    return True


def main():
    tests = [
        test_duration_hours_from_times_example,
        test_effective_duration_prefers_times_over_stored,
        test_invoice_totals_use_start_finish_not_stored_duration,
        test_calculate_from_form_data_ignores_stale_duration,
        test_preview_calculate_endpoint_matches,
        test_invoice_preview_and_pdf_match,
        test_edit_form_duration_matches_invoice,
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
