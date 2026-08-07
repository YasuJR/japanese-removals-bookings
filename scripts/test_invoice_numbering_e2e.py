#!/usr/bin/env python3
"""Invoice numbering and PDF template tests."""

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
import invoice_numbering
from integrations import invoice_pdf
from validators import parse_booking_form


class _FakeForm(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _form(**overrides):
    base = {
        "customer_name": "Number Test Customer",
        "phone": "0412000111",
        "email": "numtest@example.com",
        "pickup_address": "1 Seq St, Perth WA",
        "delivery_address": "2 Seq Ave, Fremantle WA",
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
        "invoice_status": "",
        "status": "Completed",
    }
    base.update(overrides)
    return _FakeForm(base)


def _create_booking(label: str = "Num") -> int:
    return db.create_booking(
        "{0} Customer".format(label),
        "0412000111",
        "{0}@example.com".format(label.lower()),
        "1 Seq St, Perth WA",
        "2 Seq Ave, Fremantle WA",
        "2026-08-08",
        2,
        "Invoice numbering test",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        duration_hours="1",
    )


def test_first_and_second_invoice_numbers():
    db.init_db()
    booking_a = _create_booking("First")
    booking_b = _create_booking("Second")

    number_a = invoice_numbering.ensure_booking_invoice_number(booking_a)
    number_b = invoice_numbering.ensure_booking_invoice_number(booking_b)
    assert number_a, "First invoice should receive a number"
    assert number_b, "Second invoice should receive a number"
    assert int(number_b) == int(number_a) + 1
    return number_a, number_b


def test_edit_keeps_same_number():
    import services

    db.init_db()
    booking_id = _create_booking("EditKeep")
    first = invoice_numbering.ensure_booking_invoice_number(booking_id)
    with patch("services.sync_xero_draft_if_linked", return_value=None):
        ok, errors, _msg = services.update_booking_invoice(booking_id, _form())
    assert ok and not errors, errors
    row = dict(db.get_booking(booking_id))
    assert row.get("invoice_number") == first
    return first


def test_pdf_shows_assigned_number():
    db.init_db()
    booking_id = _create_booking("Pdf")
    number = invoice_numbering.ensure_booking_invoice_number(booking_id)
    row = db.get_booking(booking_id)
    booking = dict(row)
    booking["extra_charges"] = []
    doc = invoice_pdf.build_invoice_document(booking)
    assert doc["invoice_number"] == number
    assert doc["company_abn"] == invoice_numbering.DEFAULT_ABN
    assert doc["company_contact_lines"] == [
        "Phone: 0481 089 573",
        "Email: info@japaneseremovals.com.au",
        "Website: japaneseremovals.com.au",
    ]
    pdf_bytes = invoice_pdf.generate_invoice_pdf(booking)
    assert str(number).encode("utf-8") in pdf_bytes
    return number


def test_sequence_survives_reinit():
    db.init_db()
    before = db.allocate_invoice_number()
    db.init_db()
    after = db.allocate_invoice_number()
    assert after == before + 1
    return before, after


def test_deleted_invoice_does_not_reuse_number():
    db.init_db()
    booking_id = _create_booking("Delete")
    number = invoice_numbering.ensure_booking_invoice_number(booking_id)
    db.delete_booking(booking_id)
    next_number = str(db.allocate_invoice_number())
    assert int(next_number) > int(number)
    return number, next_number


def main():
    tests = [
        ("first_and_second", test_first_and_second_invoice_numbers),
        ("edit_keeps_number", test_edit_keeps_same_number),
        ("pdf_number", test_pdf_shows_assigned_number),
        ("sequence_reinit", test_sequence_survives_reinit),
        ("no_reuse_after_delete", test_deleted_invoice_does_not_reuse_number),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS:", name)
        except Exception as exc:
            failed += 1
            print("FAIL:", name, exc)
    print("\n{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
