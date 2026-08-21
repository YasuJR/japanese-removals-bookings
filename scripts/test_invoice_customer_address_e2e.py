#!/usr/bin/env python3
"""E2E tests — Invoice BILL TO shows pickup and drop-off addresses only."""

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
from app import app
from integrations import invoice_pdf


_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "inv-addr-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Invoice Address")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_booking(
    name="Address Customer",
    pickup="12 Smith St, Subiaco WA 6008",
    delivery="88 Harbour Rd, Fremantle WA 6160",
    phone="0412555000",
    email="address@example.com",
):
    return db.create_booking(
        name,
        phone,
        email,
        pickup,
        delivery,
        "2026-09-18",
        2,
        "invoice address test",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        duration_hours="2",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
    )


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


def _bill_to_html(html):
    start = html.find("invoice-bill-section")
    end = html.find("invoice-lines", start)
    assert start != -1 and end != -1
    return html[start:end]


def test_bill_to_helper_uses_pickup_and_delivery_address():
    booking = {
        "customer_name": "Kate",
        "pickup_address": "  12 Smith St, Subiaco WA 6008 ",
        "delivery_address": "88 Harbour Rd, Fremantle WA 6160",
        "phone": "0412555000",
        "email": "kate@example.com",
    }
    bill_to = invoice.invoice_customer_bill_to(booking)
    assert bill_to["customer_name"] == "Kate"
    assert bill_to["pickup_address"] == "12 Smith St, Subiaco WA 6008"
    assert bill_to["dropoff_address"] == "88 Harbour Rd, Fremantle WA 6160"
    assert bill_to["pickup_line"] == "Pickup: 12 Smith St, Subiaco WA 6008"
    assert bill_to["dropoff_line"] == "Drop-off: 88 Harbour Rd, Fremantle WA 6160"
    assert "customer_phone" not in bill_to
    assert "customer_email" not in bill_to
    empty = invoice.invoice_customer_bill_to(
        {
            "customer_name": "No Address",
            "pickup_address": "",
            "delivery_address": "",
            "phone": "0412000000",
            "email": "office@japaneseremovals.com.au",
        }
    )
    assert empty["pickup_address"] == ""
    assert empty["dropoff_address"] == ""
    assert empty["pickup_line"] == "Pickup:"
    assert empty["dropoff_line"] == "Drop-off:"
    alt = invoice.invoice_customer_bill_to(
        {"customer_name": "Alt", "dropoff_address": "9 Alt St, Perth WA"}
    )
    assert alt["dropoff_address"] == "9 Alt St, Perth WA"
    return True


def test_document_and_pdf_include_pickup_and_dropoff():
    db.init_db()
    pickup = "12 Smith St, Subiaco WA 6008"
    dropoff = "88 Harbour Rd, Fremantle WA 6160"
    booking_id = _create_booking(pickup=pickup, delivery=dropoff)
    db.update_booking_invoice_fields(booking_id, {"invoice_number": "25"})
    before = dict(db.get_booking(booking_id))
    booking = dict(before)
    booking["extra_charges"] = db.list_extra_charges(booking_id)
    doc = invoice_pdf.build_invoice_document(booking)
    assert doc["pickup_address"] == pickup
    assert doc["dropoff_address"] == dropoff
    assert doc["pickup_line"] == "Pickup: {0}".format(pickup)
    assert doc["dropoff_line"] == "Drop-off: {0}".format(dropoff)
    assert "customer_phone" not in doc
    assert "customer_email" not in doc
    assert doc["customer_name"] == "Address Customer"
    assert doc["invoice_number"] == "INV25"
    assert doc["bank"]["payment_reference"] == "INV25"
    assert invoice.format_aud(doc["totals"]["total"]) == "$450.00"
    pdf_text = _pdf_text(invoice_pdf.generate_invoice_pdf(booking))
    assert "Pickup: {0}".format(pickup) in pdf_text
    assert "Drop-off: {0}".format(dropoff) in pdf_text
    assert "0412555000" not in pdf_text
    assert "address@example.com" not in pdf_text
    assert "INV25" in pdf_text
    after = dict(db.get_booking(booking_id))
    assert after["pickup_address"] == before["pickup_address"]
    assert after["delivery_address"] == before["delivery_address"]
    return True


def test_preview_html_shows_pickup_and_dropoff_not_phone_email():
    client = _login_client()
    pickup = "45 Stirling Hwy, Nedlands WA 6009"
    dropoff = "10 Beach St, Fremantle WA 6160"
    booking_id = _create_booking(
        name="Preview Address Customer",
        pickup=pickup,
        delivery=dropoff,
        phone="0412666777",
        email="preview-addr@example.com",
    )
    html = client.get("/bookings/{0}/invoice/preview".format(booking_id)).get_data(
        as_text=True
    )
    bill = _bill_to_html(html)
    assert "invoice-bill-to" in html
    assert "Preview Address Customer" in bill
    assert "Pickup: {0}".format(pickup) in bill
    assert "Drop-off: {0}".format(dropoff) in bill
    assert "0412666777" not in bill
    assert "preview-addr@example.com" not in bill
    assert "Phone:" not in bill
    assert "Email:" not in bill
    after = dict(db.get_booking(booking_id))
    assert after["pickup_address"] == pickup
    assert after["delivery_address"] == dropoff
    return True


def test_empty_addresses_stay_blank_without_company_contact():
    client = _login_client()
    booking_id = _create_booking(
        name="Blank Address Customer",
        pickup="",
        delivery="",
        phone="0412000000",
        email="office@example.com",
    )
    booking = dict(db.get_booking(booking_id))
    booking["extra_charges"] = []
    doc = invoice_pdf.build_invoice_document(booking)
    assert doc["pickup_address"] == ""
    assert doc["dropoff_address"] == ""
    assert doc["pickup_line"] == "Pickup:"
    assert doc["dropoff_line"] == "Drop-off:"
    html = client.get("/bookings/{0}/invoice/preview".format(booking_id)).get_data(
        as_text=True
    )
    bill = _bill_to_html(html)
    assert "Blank Address Customer" in bill
    assert "Pickup:" in bill
    assert "Drop-off:" in bill
    assert "0412000000" not in bill
    assert "office@example.com" not in bill
    pdf_text = _pdf_text(invoice_pdf.generate_invoice_pdf(booking))
    assert "0412000000" not in pdf_text
    assert "office@example.com" not in pdf_text
    assert dict(db.get_booking(booking_id))["pickup_address"] == ""
    return True


def test_pdf_route_does_not_change_address_or_totals():
    client = _login_client()
    pickup = "9 Queen St, Perth WA 6000"
    dropoff = "2 Port Rd, Fremantle WA 6160"
    booking_id = _create_booking(pickup=pickup, delivery=dropoff)
    db.update_booking_invoice_fields(
        booking_id,
        {"invoice_number": "118", "payment_status": invoice.PAYMENT_STATUS_UNPAID},
    )
    before = dict(db.get_booking(booking_id))
    resp = client.get("/bookings/{0}/invoice.pdf".format(booking_id))
    assert resp.status_code == 200
    assert resp.data.startswith(b"%PDF")
    pdf_text = _pdf_text(resp.data)
    assert "Pickup: {0}".format(pickup) in pdf_text
    assert "Drop-off: {0}".format(dropoff) in pdf_text
    assert "INV118" in pdf_text
    after = dict(db.get_booking(booking_id))
    assert after["pickup_address"] == before["pickup_address"]
    assert after["delivery_address"] == before["delivery_address"]
    assert after["payment_status"] == before["payment_status"]
    assert after["invoice_number"] == before["invoice_number"]
    return True


def main():
    tests = [
        test_bill_to_helper_uses_pickup_and_delivery_address,
        test_document_and_pdf_include_pickup_and_dropoff,
        test_preview_html_shows_pickup_and_dropoff_not_phone_email,
        test_empty_addresses_stay_blank_without_company_contact,
        test_pdf_route_does_not_change_address_or_totals,
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
    raise SystemExit(main())
