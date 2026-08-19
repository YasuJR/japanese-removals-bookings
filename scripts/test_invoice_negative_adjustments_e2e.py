#!/usr/bin/env python3
"""E2E tests — negative invoice adjustments (break, discount, credit)."""

import base64
import os
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import extra_charges
import invoice
from app import app
from integrations import invoice_pdf
from validators import parse_booking_form


_test_user_counter = 0


def _unique_move_date():
    global _test_user_counter
    day = 1 + (_test_user_counter % 27)
    return "2026-12-{0:02d}".format(day)


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "neg-adj-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Neg Adj Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_booking(**kwargs):
    move_date = kwargs.pop("move_date", None) or _unique_move_date()
    booking_id = db.create_booking(
        kwargs.get("customer_name", "Adjustment Customer"),
        kwargs.get("phone", "0412333444"),
        kwargs.get("email", "adjust@example.com"),
        "1 Adjust St, Perth WA",
        "2 Adjust Ave, Fremantle WA",
        move_date,
        2,
        "negative adjustment test",
        hourly_rate=kwargs.get("hourly_rate", 287.0),
        callout_fee=kwargs.get("callout_fee", 0.0),
        gst_enabled=1,
        start_time=kwargs.get("start_time", "09:00"),
        finish_time=kwargs.get("finish_time", "16:30"),
        duration_hours=kwargs.get("duration_hours", "7.5"),
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
    )
    return booking_id, move_date


def _form(booking_id, move_date, **overrides):
    row = dict(db.get_booking(booking_id))
    base = {
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "email": row["email"],
        "pickup_address": row["pickup_address"],
        "delivery_address": row["delivery_address"],
        "move_date": move_date,
        "num_movers": str(row["num_movers"]),
        "notes": row["notes"] or "",
        "start_time": row["start_time"] or "09:00",
        "finish_time": row["finish_time"] or "16:30",
        "duration_hours": row["duration_hours"] or "7.5",
        "hourly_rate": str(row["hourly_rate"] if row["hourly_rate"] is not None else 287),
        "callout_fee": str(row["callout_fee"] if row["callout_fee"] is not None else 0),
        "gst_enabled": "on",
        "payment_status": "Unpaid",
        "invoice_status": row["invoice_status"] or "",
        "status": "Confirmed",
        "action": "save",
        "double_booking_override_confirm": "on",
    }
    base.update(overrides)
    return base


def _booking_dict(booking_id):
    row = dict(db.get_booking(booking_id))
    row["extra_charges"] = db.list_extra_charges(booking_id)
    return row


def _pdf_text(pdf_bytes):
    chunks = []
    for part in pdf_bytes.split(b"endstream"):
        idx = part.rfind(b"stream")
        if idx < 0:
            continue
        data = part[idx + 6 :]
        if data.startswith(b"\r\n"):
            data = data[2:]
        elif data.startswith(b"\n"):
            data = data[1:]
        data = data.strip()
        try:
            decoded = base64.a85decode(data, adobe=True, ignorechars=b" \t\r\n")
        except Exception:
            continue
        try:
            decoded = zlib.decompress(decoded)
        except Exception:
            pass
        chunks.append(decoded.decode("latin-1", "ignore"))
    return "\n".join(chunks)


def test_format_aud_negative_prefix():
    assert invoice.format_aud(-143.5) == "-$143.50"
    assert invoice.format_aud(-100) == "-$100.00"
    assert invoice.format_aud(287) == "$287.00"
    assert invoice.format_aud(0) == "$0.00"
    return True


def test_break_deduction_thirty_minutes():
    assert extra_charges.break_deduction_unit_price(287, 30) == -143.50
    assert extra_charges.break_deduction_description(30) == "30 min break deduction"
    return True


def test_parse_allows_negative_unit_price():
    items, errors = extra_charges.parse_extra_charges_from_form(
        {
            "extra_description": ["30 min break deduction", "Discount"],
            "extra_quantity": ["1", "1"],
            "extra_unit_price": ["-143.50", "-100.00"],
        }
    )
    assert not errors, errors
    assert items[0]["unit_price"] == -143.50
    assert items[1]["unit_price"] == -100.00
    assert extra_charges.charges_gross_total(items) == -243.50
    return True


def test_hourly_rate_and_callout_remain_non_negative():
    data, errors = parse_booking_form(
        {
            "customer_name": "Rate Guard",
            "phone": "0412000003",
            "email": "rate@example.com",
            "pickup_address": "1 Test St, Perth WA",
            "delivery_address": "2 Test Ave, Fremantle WA",
            "move_date": "2026-12-20",
            "num_movers": "2",
            "start_time": "09:00",
            "finish_time": "16:30",
            "duration_hours": "7.5",
            "hourly_rate": "-10",
            "callout_fee": "-5",
            "gst_enabled": "on",
            "status": "Confirmed",
        }
    )
    assert any("Hourly rate" in err for err in errors)
    assert any("Callout fee" in err for err in errors)
    return True


def test_gst_and_total_with_break_and_discount():
    booking = {
        "start_time": "09:00",
        "finish_time": "16:30",
        "duration_hours": "7.5",
        "hourly_rate": 287.0,
        "callout_fee": 0.0,
        "gst_enabled": 1,
        "extra_charges": [
            {
                "description": "30 min break deduction",
                "quantity": 1,
                "unit_price": -143.50,
            },
            {"description": "Discount", "quantity": 1, "unit_price": -100.00},
        ],
    }
    totals = invoice.calculate_invoice_totals(booking)
    assert totals["hours"] == 7.5
    assert round(totals["hourly_rate"] * totals["hours"], 2) == 2152.50
    assert totals["extras_total"] == -243.50
    assert totals["total"] == 1909.00
    assert totals["subtotal"] == 1735.45
    assert totals["gst_amount"] == 173.55
    without = invoice.calculate_invoice_totals({**booking, "extra_charges": []})
    assert without["gst_amount"] > totals["gst_amount"]
    assert without["total"] > totals["total"]
    return True


def test_negative_invoice_total_rejected():
    data, errors = parse_booking_form(
        {
            "customer_name": "Too Much Discount",
            "phone": "0412000004",
            "email": "toomuch@example.com",
            "pickup_address": "1 Test St, Perth WA",
            "delivery_address": "2 Test Ave, Fremantle WA",
            "move_date": "2026-12-21",
            "num_movers": "2",
            "start_time": "09:00",
            "finish_time": "16:30",
            "duration_hours": "7.5",
            "hourly_rate": "287",
            "callout_fee": "0",
            "gst_enabled": "on",
            "status": "Confirmed",
            "extra_description": ["Discount"],
            "extra_quantity": ["1"],
            "extra_unit_price": ["-9999"],
        }
    )
    assert any("cannot be negative" in err.lower() for err in errors), errors
    return True


def test_save_reload_preview_pdf_and_unchanged_times():
    booking_id, move_date = _create_booking()
    client = _login_client()
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "Break deduction" in html
    assert "Discount" in html
    assert "Credit" in html
    assert "Other adjustment" in html
    assert 'id="extra-break-minutes"' in html
    js = client.get("/static/invoice_pricing.js").get_data(as_text=True)
    assert 'name="extra_unit_price" step="0.01"' in js
    assert 'name="extra_unit_price" min="0"' not in js

    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            extra_description=["30 min break deduction", "Discount"],
            extra_quantity=["1", "1"],
            extra_unit_price=["-143.50", "-100.00"],
        ),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code

    row = _booking_dict(booking_id)
    assert row["start_time"] == "09:00"
    assert row["finish_time"] == "16:30"
    assert row["duration_hours"] == "7.5"
    charges = row["extra_charges"]
    assert len(charges) == 2
    assert charges[0]["description"] == "30 min break deduction"
    assert float(charges[0]["unit_price"]) == -143.50
    assert charges[1]["description"] == "Discount"
    assert float(charges[1]["unit_price"]) == -100.00

    totals = invoice.calculate_invoice_totals(row)
    assert totals["hours"] == 7.5
    assert totals["total"] == 1909.00
    labour = invoice.format_moving_labour_description(row, totals)
    assert "7.5 hrs" in labour
    assert "9:00 AM - 4:30 PM" in labour

    edit_html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "30 min break deduction" in edit_html
    assert "Discount" in edit_html
    assert "-143.5" in edit_html or "-143.50" in edit_html
    assert "-100" in edit_html

    preview = client.get("/bookings/{0}/invoice/preview".format(booking_id)).get_data(
        as_text=True
    )
    assert "30 min break deduction" in preview
    assert "Discount" in preview
    assert "-$143.50" in preview
    assert "-$100.00" in preview
    assert "1.00" in preview
    assert "$1,909.00" in preview
    assert "$1,735.45" in preview
    assert "$173.55" in preview

    doc = invoice_pdf.build_invoice_document(row)
    descriptions = [item["description_html"] for item in doc["line_items"]]
    assert "30 min break deduction" in descriptions
    assert "Discount" in descriptions
    break_item = next(
        item
        for item in doc["line_items"]
        if item["description_html"] == "30 min break deduction"
    )
    assert break_item["quantity"] == "1.00"
    assert break_item["unit_price"] == "-$143.50"
    assert break_item["amount"] == "-$143.50"
    discount_item = next(
        item for item in doc["line_items"] if item["description_html"] == "Discount"
    )
    assert discount_item["unit_price"] == "-$100.00"
    assert discount_item["amount"] == "-$100.00"
    assert doc["totals"]["hours"] == 7.5
    assert doc["totals"]["total"] == 1909.00

    pdf_text = _pdf_text(invoice_pdf.generate_invoice_pdf(row))
    assert "30 min break deduction" in pdf_text
    assert "Discount" in pdf_text
    assert "-$143.50" in pdf_text or "143.50" in pdf_text
    assert "-$100.00" in pdf_text or "100.00" in pdf_text
    return True


def test_manual_negative_adjustment_only():
    booking_id, move_date = _create_booking()
    client = _login_client()
    client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            extra_description=["Credit"],
            extra_quantity=["1"],
            extra_unit_price=["-50"],
        ),
    )
    row = _booking_dict(booking_id)
    assert row["duration_hours"] == "7.5"
    assert row["start_time"] == "09:00"
    assert row["finish_time"] == "16:30"
    assert float(row["extra_charges"][0]["unit_price"]) == -50.0
    totals = invoice.calculate_invoice_totals(row)
    assert totals["hours"] == 7.5
    assert totals["extras_total"] == -50.0
    assert totals["total"] == 2102.50
    return True


def test_positive_extra_charge_still_allowed():
    items, errors = extra_charges.parse_extra_charges_from_form(
        {
            "extra_description": ["Stairs Fee"],
            "extra_quantity": ["1"],
            "extra_unit_price": ["80"],
        }
    )
    assert not errors, errors
    assert items[0]["unit_price"] == 80.0
    return True


def main():
    tests = [
        test_format_aud_negative_prefix,
        test_break_deduction_thirty_minutes,
        test_parse_allows_negative_unit_price,
        test_hourly_rate_and_callout_remain_non_negative,
        test_gst_and_total_with_break_and_discount,
        test_negative_invoice_total_rejected,
        test_save_reload_preview_pdf_and_unchanged_times,
        test_manual_negative_adjustment_only,
        test_positive_extra_charge_still_allowed,
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
