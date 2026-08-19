#!/usr/bin/env python3
"""Regression tests — Finish Time is the source of truth on Edit Booking."""

import base64
import os
import re
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import invoice
import services
from app import app
from booking_times import (
    duration_hours_from_times,
    normalize_time_input,
    validate_times,
)
from integrations import invoice_pdf
from validators import parse_booking_form


_test_user_counter = 0


def _unique_move_date():
    global _test_user_counter
    day = 1 + (_test_user_counter % 27)
    return "2026-11-{0:02d}".format(day)


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "finish-sot-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Finish SOT Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_booking(**kwargs):
    move_date = kwargs.pop("move_date", None) or _unique_move_date()
    booking_id = db.create_booking(
        kwargs.get("customer_name", "Finish SOT Customer"),
        kwargs.get("phone", "0412111222"),
        kwargs.get("email", "finish-sot@example.com"),
        "1 Finish St, Perth WA",
        "2 Finish Ave, Fremantle WA",
        move_date,
        2,
        "finish time source of truth",
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


def _input_value(html, name):
    match = re.search(
        r'<input[^>]+name="{0}"[^>]*>'.format(re.escape(name)),
        html,
        re.I,
    )
    if not match:
        return None
    value_match = re.search(r'value="([^"]*)"', match.group(0))
    return value_match.group(1) if value_match else ""


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


def test_normalize_time_input_accepts_hhmmss():
    assert normalize_time_input("16:30") == "16:30"
    assert normalize_time_input("16:30:00") == "16:30"
    assert normalize_time_input("9:00") == "09:00"
    assert normalize_time_input("09:00:00.000") == "09:00"
    assert normalize_time_input("17:00:00") == "17:00"
    assert duration_hours_from_times("09:00", "16:30") == 7.5
    assert duration_hours_from_times("09:00:00", "16:30:00") == 7.5
    return True


def test_validate_times_explicit_finish_wins_over_stale_duration():
    start, finish, duration, errors = validate_times("09:00", "16:30", "7.5")
    assert not errors, errors
    assert start == "09:00"
    assert finish == "16:30"
    assert duration == "7.5"

    start, finish, duration, errors = validate_times("09:00", "17:00:00", "7.5")
    assert not errors, errors
    assert finish == "17:00"
    assert duration == "8"

    start, finish, duration, errors = validate_times("09:00", "16:30:00", "4")
    assert not errors, errors
    assert finish == "16:30"
    assert duration == "7.5"
    return True


def test_validate_times_rejects_finish_before_start_and_keeps_finish():
    start, finish, duration, errors = validate_times("09:00", "08:00", "8")
    assert errors
    assert finish == "08:00"
    assert "after start time" in errors[0].lower()
    return True


def test_a_nine_to_four_thirty_duration_preview_pdf_qty():
    booking_id, move_date = _create_booking()
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(booking_id, move_date, start_time="09:00", finish_time="16:30", duration_hours="7.5"),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code

    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert _input_value(html, "start_time") == "09:00"
    assert _input_value(html, "finish_time") == "16:30"
    assert _input_value(html, "duration_hours") == "7.5"

    row = _booking_dict(booking_id)
    assert row["start_time"] == "09:00"
    assert row["finish_time"] == "16:30"
    assert row["duration_hours"] == "7.5"

    totals = invoice.calculate_invoice_totals(row)
    assert totals["hours"] == 7.5
    expected = "Moving Labour — 9:00 AM - 4:30 PM — 7.5 hrs @ $287.00/hr"
    assert invoice.format_moving_labour_description(row, totals) == expected
    assert invoice.resolve_labour_description(row, totals) == expected

    preview = client.get("/bookings/{0}/invoice/preview".format(booking_id)).get_data(
        as_text=True
    )
    assert "9:00 AM - 4:30 PM" in preview
    assert "7.5 hrs" in preview
    assert ">7.50<" in preview or "7.50" in preview

    doc = invoice_pdf.build_invoice_document(row)
    assert doc["line_items"][0]["description_html"] == expected
    assert doc["line_items"][0]["quantity"] == "7.50"
    assert "9:00 AM - 4:30 PM" in doc["line_items"][0]["description_html"]

    pdf_bytes = invoice_pdf.generate_invoice_pdf(row)
    assert pdf_bytes.startswith(b"%PDF")
    pdf_text = _pdf_text(pdf_bytes)
    assert "9:00 AM" in pdf_text
    assert "4:30 PM" in pdf_text
    assert "7.50" in pdf_text or "7.5" in pdf_text
    return True


def test_b_change_finish_to_five_saves_and_reloads():
    booking_id, move_date = _create_booking()
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            start_time="09:00",
            finish_time="17:00:00",
            duration_hours="7.5",
        ),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    assert "/bookings/{0}/edit".format(booking_id) in (resp.headers.get("Location") or "")

    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "Changes saved successfully." in html
    assert _input_value(html, "finish_time") == "17:00"
    assert _input_value(html, "duration_hours") == "8"

    row = _booking_dict(booking_id)
    assert row["finish_time"] == "17:00"
    assert row["duration_hours"] == "8"
    totals = invoice.calculate_invoice_totals(row)
    assert totals["hours"] == 8.0
    expected = "Moving Labour — 9:00 AM - 5:00 PM — 8 hrs @ $287.00/hr"
    assert invoice.resolve_labour_description(row, totals) == expected

    preview = client.get("/bookings/{0}/invoice/preview".format(booking_id)).get_data(
        as_text=True
    )
    assert "9:00 AM - 5:00 PM" in preview
    assert "8 hrs" in preview
    assert "4:30 PM" not in preview

    doc = invoice_pdf.build_invoice_document(row)
    assert doc["line_items"][0]["quantity"] == "8.00"
    assert "5:00 PM" in doc["line_items"][0]["description_html"]
    pdf_text = _pdf_text(invoice_pdf.generate_invoice_pdf(row))
    assert "5:00 PM" in pdf_text
    assert "4:30 PM" not in pdf_text
    return True


def test_c_change_start_recalculates_duration():
    booking_id, move_date = _create_booking()
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            start_time="08:00",
            finish_time="16:30",
            duration_hours="7.5",
        ),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert _input_value(html, "start_time") == "08:00"
    assert _input_value(html, "finish_time") == "16:30"
    assert _input_value(html, "duration_hours") == "8.5"

    row = _booking_dict(booking_id)
    assert row["start_time"] == "08:00"
    assert row["finish_time"] == "16:30"
    assert row["duration_hours"] == "8.5"
    totals = invoice.calculate_invoice_totals(row)
    assert totals["hours"] == 8.5
    desc = invoice.format_moving_labour_description(row, totals)
    assert "8:00 AM - 4:30 PM" in desc
    assert "8.5 hrs" in desc
    return True


def test_d_manual_invoice_description_unchanged_after_finish_change():
    booking_id, move_date = _create_booking()
    client = _login_client()
    custom = "Do not auto-replace this description"
    client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            invoice_description=custom,
            invoice_description_custom="1",
        ),
    )
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            start_time="09:00",
            finish_time="17:00",
            duration_hours="7.5",
            invoice_description=custom,
            invoice_description_custom="1",
        ),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    row = _booking_dict(booking_id)
    assert row["finish_time"] == "17:00"
    assert row.get("invoice_description") == custom
    totals = invoice.calculate_invoice_totals(row)
    auto = invoice.format_moving_labour_description(row, totals)
    assert "5:00 PM" in auto
    assert invoice.resolve_labour_description(row, totals) == custom
    preview = client.get("/bookings/{0}/invoice/preview".format(booking_id)).get_data(
        as_text=True
    )
    assert custom in preview
    assert auto not in preview
    doc = invoice_pdf.build_invoice_document(row)
    assert custom in doc["line_items"][0]["description_html"]
    assert auto not in doc["line_items"][0]["description_html"]
    return True


def test_e_unedited_bookings_keep_auto_moving_labour_description():
    booking_id, move_date = _create_booking()
    client = _login_client()
    row = _booking_dict(booking_id)
    assert not (row.get("invoice_description") or "").strip()
    expected = "Moving Labour — 9:00 AM - 4:30 PM — 7.5 hrs @ $287.00/hr"
    totals = invoice.calculate_invoice_totals(row)
    assert invoice.resolve_labour_description(row, totals) == expected

    client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            start_time="09:00",
            finish_time="16:30",
            duration_hours="7.5",
            invoice_description=expected,
            invoice_description_custom="0",
        ),
    )
    row = _booking_dict(booking_id)
    assert not (row.get("invoice_description") or "").strip()
    assert invoice.resolve_labour_description(row) == expected

    client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            start_time="09:00",
            finish_time="17:00",
            duration_hours="7.5",
            invoice_description=expected,
            invoice_description_custom="0",
        ),
    )
    row = _booking_dict(booking_id)
    assert not (row.get("invoice_description") or "").strip()
    resolved = invoice.resolve_labour_description(row)
    assert "9:00 AM - 5:00 PM" in resolved
    assert "8 hrs" in resolved
    return True


def test_stale_duration_does_not_block_save():
    data, errors = parse_booking_form(
        {
            "customer_name": "Stale Duration",
            "phone": "0412000002",
            "email": "stale@example.com",
            "pickup_address": "1 Test St, Perth WA",
            "delivery_address": "2 Test Ave, Fremantle WA",
            "move_date": "2026-11-20",
            "num_movers": "2",
            "start_time": "09:00",
            "finish_time": "17:00",
            "duration_hours": "7.5",
            "hourly_rate": "287",
            "callout_fee": "0",
            "gst_enabled": "on",
            "status": "Confirmed",
        }
    )
    assert not errors, errors
    assert data["finish_time"] == "17:00"
    assert data["duration_hours"] == "8"
    return True


def test_edit_page_has_editable_start_and_finish():
    booking_id, _move_date = _create_booking()
    client = _login_client()
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert re.search(
        r'<input[^>]+type="time"[^>]+name="start_time"',
        html,
    )
    assert re.search(
        r'<input[^>]+type="time"[^>]+name="finish_time"',
        html,
    )
    assert _input_value(html, "finish_time") == "16:30"
    return True


def main():
    tests = [
        test_normalize_time_input_accepts_hhmmss,
        test_validate_times_explicit_finish_wins_over_stale_duration,
        test_validate_times_rejects_finish_before_start_and_keeps_finish,
        test_a_nine_to_four_thirty_duration_preview_pdf_qty,
        test_b_change_finish_to_five_saves_and_reloads,
        test_c_change_start_recalculates_duration,
        test_d_manual_invoice_description_unchanged_after_finish_change,
        test_e_unedited_bookings_keep_auto_moving_labour_description,
        test_stale_duration_does_not_block_save,
        test_edit_page_has_editable_start_and_finish,
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
