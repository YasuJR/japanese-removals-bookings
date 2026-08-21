#!/usr/bin/env python3
"""E2E tests — invoice PDF fits on one A4 page for typical invoices."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
import invoice
import invoice_numbering
from integrations import invoice_pdf


def _page_count(pdf_bytes: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf_bytes))


def _sample_booking(**fields):
    booking_id = db.create_booking(
        fields.get("customer_name", "PDF Layout Test"),
        "0412000222",
        "pdf-layout@example.com",
        "1 Layout St, Perth WA",
        "2 Layout Ave, Fremantle WA",
        "2026-09-15",
        2,
        "PDF layout test",
        hourly_rate=fields.get("hourly_rate", 130.0),
        callout_fee=fields.get("callout_fee", 90.0),
        gst_enabled=1,
        duration_hours="1",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
    )
    db.update_booking_invoice_fields(
        booking_id,
        {
            "invoice_number": fields.get("invoice_number", "25"),
            "invoice_status": "AUTHORISED",
        },
    )
    row = dict(db.get_booking(booking_id))
    row["extra_charges"] = db.list_extra_charges(booking_id)
    return row


def test_typical_invoice_single_page():
    db.init_db()
    booking = _sample_booking(invoice_number="25")
    doc = invoice_pdf.build_invoice_document(booking)
    assert doc["invoice_number"] == "INV25"
    assert doc["bank"]["payment_reference"] == "INV25"
    assert doc["customer_address"] == "1 Layout St, Perth WA"
    assert invoice.format_aud(doc["totals"]["total"]) == "$220.00"
    pdf_bytes = invoice_pdf.generate_invoice_pdf(booking)
    assert _page_count(pdf_bytes) == 1, "Expected 1 page, got {0}".format(
        _page_count(pdf_bytes)
    )
    return True


def test_booking_id_fallback_single_page():
    db.init_db()
    booking = _sample_booking(invoice_number="")
    db.update_booking_invoice_fields(int(booking["id"]), {"invoice_number": ""})
    booking = dict(db.get_booking(int(booking["id"])))
    booking["extra_charges"] = []
    doc = invoice_pdf.build_invoice_document(booking)
    expected = "INV{0}".format(booking["id"])
    assert doc["invoice_number"] == expected
    assert doc["bank"]["payment_reference"] == expected
    assert _page_count(invoice_pdf.generate_invoice_pdf(booking)) == 1
    return True


def test_many_line_items_may_use_second_page():
    db.init_db()
    booking = _sample_booking(invoice_number="100")
    charges = []
    for idx in range(12):
        charges.append(
            {
                "description": "Extra charge item {0}".format(idx + 1),
                "quantity": 1.0,
                "unit_price": 25.0,
            }
        )
    booking["extra_charges"] = charges
    pages = _page_count(invoice_pdf.generate_invoice_pdf(booking))
    assert pages >= 1
    assert pages <= 3
    return True


def test_invoice_line_items_exclude_crew():
    db.init_db()
    booking = _sample_booking(invoice_number="26")
    booking["crew"] = "Yasu,Tom,Ken"
    doc = invoice_pdf.build_invoice_document(booking)
    labour_html = doc["line_items"][0]["description_html"]
    assert "Crew" not in labour_html
    assert "Yasu" not in labour_html
    assert "Moving Labour" in labour_html
    return True


def main():
    tests = [
        test_typical_invoice_single_page,
        test_booking_id_fallback_single_page,
        test_many_line_items_may_use_second_page,
        test_invoice_line_items_exclude_crew,
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
