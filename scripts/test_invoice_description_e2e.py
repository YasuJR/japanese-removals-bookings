#!/usr/bin/env python3
"""E2E tests — editable Invoice Description on Edit Booking, preview, and PDF."""

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
import invoice
import services
from app import app
from integrations import invoice_pdf
from validators import parse_booking_form


_test_user_counter = 0


def _unique_move_date():
    global _test_user_counter
    day = 1 + (_test_user_counter % 27)
    return "2026-10-{0:02d}".format(day)


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "inv-desc-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Invoice Desc Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_booking(**kwargs):
    move_date = kwargs.pop("move_date", None) or _unique_move_date()
    booking_id = db.create_booking(
        kwargs.get("customer_name", "Description Customer"),
        kwargs.get("phone", "0412000888"),
        kwargs.get("email", "desc@example.com"),
        "1 Desc St, Perth WA",
        "2 Desc Ave, Fremantle WA",
        move_date,
        2,
        "invoice description test",
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
    """Best-effort text extraction from ReportLab ASCII85+Flate streams."""
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
            decoded = base64.a85decode(
                data, adobe=True, ignorechars=b" \t\r\n"
            )
        except Exception:
            continue
        try:
            decoded = zlib.decompress(decoded)
        except Exception:
            pass
        chunks.append(decoded.decode("latin-1", "ignore"))
    return "\n".join(chunks)


def test_column_added_and_nullable():
    db.init_db()
    with db.get_connection() as conn:
        columns = db_backend_columns(conn)
    assert "invoice_description" in columns
    booking_id, _move_date = _create_booking()
    row = dict(db.get_booking(booking_id))
    assert not (row.get("invoice_description") or "").strip()
    return True


def db_backend_columns(conn):
    import db_backend

    return db_backend.table_columns(conn, "bookings")


def test_unedited_booking_uses_auto_description():
    booking_id, _move_date = _create_booking()
    booking = _booking_dict(booking_id)
    totals = invoice.calculate_invoice_totals(booking)
    expected = "Moving Labour — 9:00 AM - 4:30 PM — 7.5 hrs @ $287.00/hr"
    assert invoice.format_moving_labour_description(booking, totals) == expected
    assert invoice.resolve_labour_description(booking, totals) == expected
    doc = invoice_pdf.build_invoice_document(booking)
    assert doc["line_items"][0]["description_html"] == expected
    return True


def test_edit_page_shows_invoice_description_field():
    booking_id, _move_date = _create_booking()
    client = _login_client()
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "Invoice Description" in html
    assert 'name="invoice_description"' in html
    assert "Moving Labour — 9:00 AM - 4:30 PM — 7.5 hrs @ $287.00/hr" in html
    assert 'name="invoice_description_custom"' in html
    assert 'value="0"' in html
    return True


def test_save_custom_description_and_reload():
    booking_id, move_date = _create_booking()
    client = _login_client()
    custom = "Custom labour notes\nSecond line of description"
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            invoice_description=custom,
            invoice_description_custom="1",
        ),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    row = dict(db.get_booking(booking_id))
    assert row.get("invoice_description") == custom
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "Custom labour notes" in html
    assert "Second line of description" in html
    assert 'id="invoice_description_custom"' in html
    assert 'value="1"' in html
    return True


def test_preview_and_pdf_use_saved_description():
    booking_id, move_date = _create_booking()
    client = _login_client()
    custom = "Moving Labour — packed piano\nIncludes stairs and long carry"
    client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            invoice_description=custom,
            invoice_description_custom="1",
        ),
    )
    booking = _booking_dict(booking_id)
    doc = invoice_pdf.build_invoice_document(booking)
    markup = invoice.plain_text_to_invoice_markup(custom)
    assert doc["line_items"][0]["description_html"] == markup
    assert "<br/>" in markup

    preview = client.get(
        "/bookings/{0}/invoice/preview".format(booking_id)
    ).get_data(as_text=True)
    assert "Moving Labour — packed piano" in preview
    assert "Includes stairs and long carry" in preview
    assert "<br/>" in preview or "<br>" in preview

    pdf_bytes = invoice_pdf.generate_invoice_pdf(booking)
    assert pdf_bytes.startswith(b"%PDF")
    pdf_text = _pdf_text(pdf_bytes)
    assert "packed piano" in pdf_text
    assert "stairs and long carry" in pdf_text
    return True


def test_time_change_does_not_reset_custom_description():
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
            start_time="10:00",
            finish_time="15:00",
            duration_hours="5",
            invoice_description=custom,
            invoice_description_custom="1",
        ),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    row = dict(db.get_booking(booking_id))
    assert row.get("start_time") == "10:00"
    assert row.get("finish_time") == "15:00"
    assert row.get("invoice_description") == custom
    booking = _booking_dict(booking_id)
    totals = invoice.calculate_invoice_totals(booking)
    auto = invoice.format_moving_labour_description(booking, totals)
    assert "10:00 AM - 3:00 PM" in auto or "10:00 AM" in auto
    assert invoice.resolve_labour_description(booking, totals) == custom
    doc = invoice_pdf.build_invoice_document(booking)
    assert custom in doc["line_items"][0]["description_html"]
    assert auto not in doc["line_items"][0]["description_html"]
    return True


def test_unedited_save_keeps_auto_fallback_after_time_change():
    booking_id, move_date = _create_booking()
    client = _login_client()
    auto_before = "Moving Labour — 9:00 AM - 4:30 PM — 7.5 hrs @ $287.00/hr"
    client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            move_date,
            invoice_description=auto_before,
            invoice_description_custom="0",
            start_time="08:00",
            finish_time="12:00",
            duration_hours="4",
        ),
    )
    row = dict(db.get_booking(booking_id))
    assert not (row.get("invoice_description") or "").strip()
    booking = _booking_dict(booking_id)
    resolved = invoice.resolve_labour_description(booking)
    assert "8:00 AM - 12:00 PM" in resolved
    assert "4 hrs" in resolved
    return True


def test_html_is_escaped_and_newlines_become_breaks():
    text = "Line 1 <script>alert(1)</script>\nLine 2 & more"
    markup = invoice.plain_text_to_invoice_markup(text)
    assert "&lt;script&gt;" in markup
    assert "<script>" not in markup
    assert "&amp;" in markup
    assert "<br/>" in markup
    return True


def test_long_description_wraps_in_pdf_document():
    booking_id, _move_date = _create_booking()
    long_line = (
        "Moving Labour — packed piano, stairs, long carry, disassembly, "
        "reassembly, and wrapping of fragile antiques for a very long hallway "
        "access job that must wrap inside the invoice description column."
    )
    db.update_booking_invoice_fields(
        booking_id, {"invoice_description": long_line + "\nSecond paragraph."}
    )
    booking = _booking_dict(booking_id)
    doc = invoice_pdf.build_invoice_document(booking)
    html = doc["line_items"][0]["description_html"]
    assert "<br/>" in html
    assert "fragile antiques" in html
    pdf_bytes = invoice_pdf.generate_invoice_pdf(booking)
    assert pdf_bytes.startswith(b"%PDF")
    pdf_text = _pdf_text(pdf_bytes)
    assert "fragile" in pdf_text
    assert "antiques" in pdf_text
    assert "Second paragraph" in pdf_text
    return True


def test_parse_without_field_does_not_mark_present():
    data, errors = parse_booking_form(
        {
            "customer_name": "No Desc Field",
            "phone": "0412000001",
            "email": "nodesc@example.com",
            "pickup_address": "1 Test St, Perth WA",
            "delivery_address": "2 Test Ave, Fremantle WA",
            "move_date": "2026-10-15",
            "num_movers": "2",
            "start_time": "09:00",
            "finish_time": "16:30",
            "duration_hours": "7.5",
            "hourly_rate": "287",
            "callout_fee": "0",
            "gst_enabled": "on",
            "status": "Confirmed",
        }
    )
    assert not errors, errors
    assert data["invoice_description_present"] is False
    return True


def test_calculate_returns_auto_labour_description():
    booking_id, move_date = _create_booking()
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/invoice/calculate".format(booking_id),
        data=_form(booking_id, move_date),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert "labour_description" in payload
    assert "9:00 AM - 4:30 PM" in payload["labour_description"]
    assert "$287.00/hr" in payload["labour_description"]
    return True


def test_existing_custom_not_cleared_when_field_omitted():
    booking_id, move_date = _create_booking()
    db.update_booking_invoice_fields(
        booking_id, {"invoice_description": "Keep this custom text"}
    )
    ok, errors, data = services.update_booking_invoice(
        booking_id,
        {
            "customer_name": "Description Customer",
            "phone": "0412000888",
            "email": "desc@example.com",
            "pickup_address": "1 Desc St, Perth WA",
            "delivery_address": "2 Desc Ave, Fremantle WA",
            "move_date": move_date,
            "num_movers": "2",
            "notes": "invoice description test",
            "start_time": "09:00",
            "finish_time": "16:30",
            "duration_hours": "7.5",
            "hourly_rate": "287",
            "callout_fee": "0",
            "gst_enabled": "on",
            "payment_status": "Unpaid",
            "status": "Confirmed",
        },
    )
    assert ok and not errors, errors
    row = dict(db.get_booking(booking_id))
    assert row.get("invoice_description") == "Keep this custom text"
    return True


def main():
    db.init_db()
    tests = [
        test_column_added_and_nullable,
        test_unedited_booking_uses_auto_description,
        test_edit_page_shows_invoice_description_field,
        test_save_custom_description_and_reload,
        test_preview_and_pdf_use_saved_description,
        test_time_change_does_not_reset_custom_description,
        test_unedited_save_keeps_auto_fallback_after_time_change,
        test_html_is_escaped_and_newlines_become_breaks,
        test_long_description_wraps_in_pdf_document,
        test_parse_without_field_does_not_mark_present,
        test_calculate_returns_auto_labour_description,
        test_existing_custom_not_cleared_when_field_omitted,
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
