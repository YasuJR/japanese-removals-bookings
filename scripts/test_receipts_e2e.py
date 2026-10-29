#!/usr/bin/env python3
"""E2E — payment receipts (separate from the invoice).

  A  Unpaid booking → no receipt (button disabled, routes refuse)
  B  Paid booking → Generate, Receipt Preview and PDF work
  C  Invoice $1,125 → Receipt Total Paid $1,125
  D  GST: receipt Subtotal / GST / Total equal the invoice's
  E  Booking already Paid before receipts existed (incl. no paid_at, Xero-synced) → receipt later
  F  PDF served as an attachment with a valid PDF body (iPhone opens it via the share sheet)
Plus: receipt numbers (RCT + invoice number, unique, kept on update), payment date
(actual paid date, not the receipt date), payment method recording (Mark as Paid,
bank match, Stripe), and the invoice stays a TAX INVOICE.
"""

import os
import base64
import re
import sys
import zlib
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import bank_transfer_match
import database as db
import invoice
import receipts
import services
from app import app
from integrations import invoice_pdf

_counter = 0


def _client():
    global _counter
    _counter += 1
    db.init_db()
    label = "receipt-{0}-{1}".format(os.getpid(), _counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Receipt Test", is_admin=1)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _booking(rate=225.0, start="08:00", finish="13:00", callout_minutes=0, gst=1, extras=None):
    bid = db.create_booking(
        "Receipt Customer {0}".format(_counter),
        "0412000888",
        "receipt@example.com",
        "1 Receipt St, Perth WA",
        "2 Receipt Ave, Fremantle WA",
        "2026-10-20",
        2,
        "receipt test",
        hourly_rate=rate,
        callout_fee=round(rate * callout_minutes / 60, 2),
        callout_minutes=callout_minutes,
        gst_enabled=gst,
        start_time=start,
        finish_time=finish,
        duration_hours="",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
        crew="Yasu",
        status="Completed",
    )
    if extras:
        db.replace_extra_charges(bid, extras)
    return bid


def _booking_dict(bid):
    return services.booking_to_dict(db.get_booking(bid))


def _pdf_text(data):
    out = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        chunk = match.group(1)
        if chunk.rstrip().endswith(b"~>"):  # ReportLab: ASCII85 then Flate
            try:
                chunk = base64.a85decode(chunk.strip(), adobe=True)
            except ValueError:
                pass
        try:
            chunk = zlib.decompress(chunk)
        except zlib.error:
            pass
        out.append(chunk.decode("latin-1"))
    return "\n".join(out)


def _issue(client, bid, method="Bank Transfer", paid_on=None):
    if paid_on is None:
        paid_on = (date.today() - timedelta(days=2)).isoformat()
    return client.post(
        "/bookings/{0}/receipt".format(bid),
        data={"payment_method": method, "payment_date": paid_on, "return_to": "edit"},
    )


def test_case_a_unpaid_no_receipt():
    client = _client()
    bid = _booking()
    html = client.get("/bookings/{0}/edit".format(bid)).get_data(as_text=True)
    assert "Receipt available after payment" in html
    assert re.search(r'<button type="button" class="btn-secondary btn-sm" disabled>Generate Receipt</button>', html)
    view = client.get("/bookings/{0}".format(bid)).get_data(as_text=True)
    assert "Receipt available after payment" in view
    resp = _issue(client, bid)
    assert resp.status_code == 302
    assert not dict(db.get_booking(bid)).get("receipt_number")
    for path in ("/bookings/{0}/receipt/preview", "/bookings/{0}/receipt.pdf"):
        r = client.get(path.format(bid))
        assert r.status_code == 302, (path, r.status_code)
    return True


def test_case_b_c_paid_receipt_preview_pdf_and_total():
    client = _client()
    # 5 hrs × $225 = $1,125 (GST inclusive)
    bid = _booking(rate=225.0, start="08:00", finish="13:00")
    assert invoice.calculate_invoice_totals(_booking_dict(bid))["total"] == 1125.0
    resp = client.post("/bookings/{0}/edit".format(bid), data={"action": "mark_paid", "payment_method": "Bank Transfer"})
    assert resp.status_code == 302
    row = dict(db.get_booking(bid))
    assert row["payment_status"] == "Paid" and row["payment_method"] == "Bank Transfer" and row["paid_at"]

    html = client.get("/bookings/{0}/edit".format(bid)).get_data(as_text=True)
    assert ">Generate Receipt</button>" in html and 'name="payment_date"' in html
    assert "Bank Transfer\" selected" in html.replace("'", '"') or 'value="Bank Transfer" selected' in html

    paid_on = row["paid_at"][:10]
    _issue(client, bid, method="Bank Transfer", paid_on=paid_on)
    row = dict(db.get_booking(bid))
    expected_number = "RCT{0}".format(invoice_numbering_ref(row))
    assert row["receipt_number"] == expected_number, (row["receipt_number"], expected_number)
    assert float(row["amount_paid"]) == 1125.0 and row["receipt_issued_at"]

    preview = client.get("/bookings/{0}/receipt/preview".format(bid))
    assert preview.status_code == 200
    page = preview.get_data(as_text=True)
    for text in ("RECEIPT", "PAID IN FULL", expected_number, "Receipt Number", "Invoice Number", "Booking Number",
                 "#{0}".format(bid), "Payment Date", "RECEIVED FROM", "Pickup: 1 Receipt St", "Drop-off: 2 Receipt Ave",
                 "TOTAL PAID AUD (incl. GST)", "$1,125.00", "Bank Transfer", "Balance Due", "93 645 845 227",
                 "Print", "Download Receipt PDF"):
        assert text in page, text
    assert "TAX INVOICE" not in page
    assert "PAYMENT DETAILS" not in page  # no "please pay" bank box on a receipt

    pdf = client.get("/bookings/{0}/receipt.pdf".format(bid))
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf"
    assert pdf.data[:4] == b"%PDF"
    text = _pdf_text(pdf.data)
    for needle in ("RECEIPT", "PAID IN FULL", expected_number, "TOTAL PAID AUD", "1,125.00", "Bank Transfer", "RECEIVED FROM"):
        assert needle in text, needle

    # The invoice is untouched and still a tax invoice.
    inv = client.get("/bookings/{0}/invoice.pdf".format(bid))
    assert inv.status_code == 200 and "TAX INVOICE" in _pdf_text(inv.data) and "RECEIPT" not in _pdf_text(inv.data)
    assert invoice.calculate_invoice_totals(_booking_dict(bid))["total"] == 1125.0
    return True


def invoice_numbering_ref(row):
    import invoice_numbering

    return invoice_numbering.reference_number_for_booking(row)


def test_case_d_gst_figures_match_invoice():
    client = _client()
    for gst in (1, 0):
        bid = _booking(
            rate=235.0, start="08:00", finish="11:30", callout_minutes=45, gst=gst,
            extras=[{"description": "30 min break deduction", "quantity": 1, "unit_price": -117.5}],
        )
        invoice.apply_payment_status(bid, invoice.PAYMENT_STATUS_PAID)
        _issue(client, bid, method="Cash", paid_on=date.today().isoformat())
        booking = _booking_dict(bid)
        inv = invoice_pdf.build_invoice_document(booking)
        rct = invoice_pdf.build_receipt_document(booking)
        for key in ("subtotal", "gst_amount", "total", "net_sales"):
            assert rct["totals"][key] == inv["totals"][key], (gst, key)
        assert rct["total_paid"] == inv["totals"]["total"]
        assert [i["amount"] for i in rct["line_items"]] == [i["amount"] for i in inv["line_items"]]
        page = client.get("/bookings/{0}/receipt/preview".format(bid)).get_data(as_text=True)
        assert invoice.format_aud(inv["totals"]["total"]) in page
        if gst:
            assert "Subtotal (ex GST)" in page and invoice.format_aud(inv["totals"]["subtotal"]) in page
            assert "GST (10%)" in page and invoice.format_aud(inv["totals"]["gst_amount"]) in page
        else:
            assert "Not charged" in page and "TOTAL PAID AUD<" in page.replace(" </span>", "<").replace("</span>", "<")
    return True


def test_case_e_existing_paid_bookings_later():
    client = _client()
    # Paid long ago, before receipts existed, with no paid_at recorded at all.
    bid = _booking()
    db.update_booking_invoice_fields(bid, {"payment_status": "Paid", "paid_at": ""})
    state = receipts.state(_booking_dict(bid))
    assert state["paid"] and not state["issued"] and not state["payment_date_recorded"] and state["payment_date"] == ""
    resp = _issue(client, bid, method="Bank Transfer", paid_on="")
    assert not dict(db.get_booking(bid)).get("receipt_number"), "payment date is required when none was recorded"
    _issue(client, bid, method="Bank Transfer", paid_on="2026-10-21")
    row = dict(db.get_booking(bid))
    assert row["receipt_number"] and row["paid_at"] == "2026-10-21"
    assert "21 October 2026" in client.get("/bookings/{0}/receipt/preview".format(bid)).get_data(as_text=True)

    # Paid via Xero sync (paid_at = Xero FullyPaidOnDate, no method recorded).
    bid2 = _booking()
    invoice.apply_payment_status(bid2, invoice.PAYMENT_STATUS_PAID, paid_at="2026-10-22T00:00:00")
    state = receipts.state(_booking_dict(bid2))
    assert state["paid"] and state["payment_date"] == "2026-10-22" and state["payment_method"] == ""
    _issue(client, bid2, method="Bank Transfer", paid_on=state["payment_date"])
    row2 = dict(db.get_booking(bid2))
    assert row2["paid_at"] == "2026-10-22T00:00:00", "an unchanged payment date keeps the recorded paid_at"
    assert client.get("/bookings/{0}/receipt.pdf".format(bid2)).status_code == 200
    return True


def test_case_f_pdf_for_iphone():
    client = _client()
    bid = _booking()
    invoice.apply_payment_status(bid, invoice.PAYMENT_STATUS_PAID, payment_method="Card")
    _issue(client, bid, method="Card", paid_on=date.today().isoformat())
    number = dict(db.get_booking(bid))["receipt_number"]
    resp = client.get("/bookings/{0}/receipt.pdf".format(bid))
    assert resp.headers["Content-Type"] == "application/pdf"
    assert resp.headers["Content-Disposition"] == "attachment; filename=receipt-{0}.pdf".format(number)
    assert resp.data[:5] == b"%PDF-" and resp.data.rstrip().endswith(b"%%EOF")
    assert b"/MediaBox [ 0 0 595.2756 841.8898 ]" in resp.data  # A4
    preview = client.get("/bookings/{0}/receipt/preview".format(bid)).get_data(as_text=True)
    assert 'name="viewport"' in preview and "invoice_share.js" in preview and "data-share-invoice-pdf" in preview
    return True


def test_receipt_numbers_unique_and_stable():
    client = _client()
    a = _booking()
    b = _booking()
    for bid in (a, b):
        invoice.apply_payment_status(bid, invoice.PAYMENT_STATUS_PAID)
    shared = str(800000 + a)  # unique per run
    db.update_booking_invoice_fields(a, {"invoice_number": shared})
    db.update_booking_invoice_fields(b, {"invoice_number": shared})  # legacy duplicate invoice number
    _issue(client, a)
    _issue(client, b)
    na = dict(db.get_booking(a))["receipt_number"]
    nb = dict(db.get_booking(b))["receipt_number"]
    assert na == "RCT" + shared and nb == "RCT{0}-{1}".format(shared, b), (na, nb)
    # Updating the receipt (new method) keeps the number and the first issue time.
    issued = dict(db.get_booking(a))["receipt_issued_at"]
    _issue(client, a, method="Cash")
    row = dict(db.get_booking(a))
    assert row["receipt_number"] == na and row["receipt_issued_at"] == issued and row["payment_method"] == "Cash"
    # Future payment dates are refused.
    ok, msg = receipts.issue(a, method="Cash", paid_on=(date.today() + timedelta(days=3)).isoformat())
    assert not ok and "future" in msg
    return True


def test_invoice_change_after_receipt_is_flagged():
    client = _client()
    bid = _booking()
    invoice.apply_payment_status(bid, invoice.PAYMENT_STATUS_PAID)
    _issue(client, bid)
    db.replace_extra_charges(bid, [{"description": "Stairs", "quantity": 1, "unit_price": 50}])
    state = receipts.state(_booking_dict(bid))
    assert state["invoice_changed"] and state["amount_paid"] == 1125.0 and state["total"] == 1175.0
    assert "has changed since this receipt was issued" in client.get("/bookings/{0}/edit".format(bid)).get_data(as_text=True)
    return True


def test_bank_match_records_date_and_method():
    bid = _booking()
    assert bank_transfer_match.apply_paid_if_matched(
        {"match_status": bank_transfer_match.STATUS_PAID, "matched_booking_id": bid}, "2026-10-19"
    )
    row = dict(db.get_booking(bid))
    assert row["payment_status"] == "Paid" and row["paid_at"] == "2026-10-19" and row["payment_method"] == "Bank Transfer"
    assert receipts.state(_booking_dict(bid))["payment_date"] == "2026-10-19"
    return True


def main():
    tests = [
        test_case_a_unpaid_no_receipt,
        test_case_b_c_paid_receipt_preview_pdf_and_total,
        test_case_d_gst_figures_match_invoice,
        test_case_e_existing_paid_bookings_later,
        test_case_f_pdf_for_iphone,
        test_receipt_numbers_unique_and_stable,
        test_invoice_change_after_receipt_is_flagged,
        test_bank_match_records_date_and_method,
    ]
    passed = 0
    for test in tests:
        try:
            if test():
                print("PASS:", test.__name__)
                passed += 1
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            print("FAIL:", test.__name__, "—", exc)
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
