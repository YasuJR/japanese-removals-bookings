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
    formatted = invoice_numbering.format_invoice_number(number)
    assert doc["invoice_number"] == formatted
    assert formatted.startswith("INV")
    assert "-" not in formatted
    assert doc["bank"]["payment_reference"] == formatted
    assert doc["company_abn"] == invoice_numbering.DEFAULT_ABN
    assert doc["company_contact_lines"] == [
        "Phone: 0481 089 573",
        "Email: info@japaneseremovals.com.au",
        "Website: japaneseremovals.com.au",
    ]
    pdf_bytes = invoice_pdf.generate_invoice_pdf(booking)
    assert len(pdf_bytes) > 1000
    return number


def test_format_existing_numeric_invoice():
    assert invoice_numbering.format_invoice_number("25") == "INV25"
    assert invoice_numbering.format_invoice_number("100") == "INV100"
    assert invoice_numbering.format_invoice_number("INV25") == "INV25"
    assert invoice_numbering.format_invoice_number("INV-25") == "INV25"
    assert invoice_numbering.format_invoice_number("INV-0025") == "INV25"
    assert invoice_numbering.numeric_sequence_value("INV25") == 25
    assert invoice_numbering.numeric_sequence_value("INV 25") == 25
    assert invoice_numbering.format_invoice_number("INV 25") == "INV25"
    booking = {"invoice_number": "25"}
    assert invoice_numbering.display_invoice_number(booking) == "INV25"
    assert invoice_numbering.stored_invoice_number_display(booking) == "INV25"
    assert invoice_numbering.stored_invoice_number_display({"id": 25}) == ""
    return True


def test_booking_id_fallback_when_no_stored_number():
    booking = {"id": 25, "invoice_number": ""}
    assert invoice_numbering.display_invoice_number(booking) == "INV25"
    doc = invoice_pdf.build_invoice_document({**booking, "extra_charges": []})
    assert doc["invoice_number"] == "INV25"
    assert doc["bank"]["payment_reference"] == "INV25"
    return True


def test_invoice_preview_no_reference_field():
    import auth
    from app import app

    db.init_db()
    booking_id = _create_booking("Preview")
    db.update_booking_invoice_fields(booking_id, {"invoice_number": "25"})
    uid = db.create_staff_user(
        "inv-preview-{0}".format(booking_id),
        auth.hash_password("test"),
        "Preview",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "inv-preview-{0}".format(booking_id)
    html = client.get("/bookings/{0}/invoice/preview".format(booking_id)).get_data(as_text=True)
    assert "INV-0025" not in html
    assert "INV25" in html
    assert ">Reference</td>" not in html
    assert 'viewport' in html
    return True


def test_payment_reference_matches_invoice_number():
    db.init_db()
    booking_id = _create_booking("RefMatch")
    invoice_numbering.ensure_booking_invoice_number(booking_id)
    db.update_booking_invoice_fields(booking_id, {"invoice_number": "42"})
    booking = dict(db.get_booking(booking_id))
    doc = invoice_pdf.build_invoice_document(booking)
    assert doc["invoice_number"] == "INV42"
    assert doc["bank"]["payment_reference"] == "INV42"
    return True


def test_reference_25_without_stored_invoice_number():
    db.init_db()
    booking_id = _create_booking("Ref25")
    db.update_booking_invoice_fields(booking_id, {"invoice_number": ""})
    booking = dict(db.get_booking(booking_id))
    doc = invoice_pdf.build_invoice_document(booking)
    expected = "INV{0}".format(booking_id)
    assert doc["invoice_number"] == expected
    assert doc["bank"]["payment_reference"] == expected
    assert doc["invoice_number"] == doc["bank"]["payment_reference"]
    return True


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


def _force_sequence(next_number: int) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE invoice_sequence SET next_number = ? WHERE id = 1",
            (next_number,),
        )
        conn.commit()


def test_stored_inv25_counts_toward_max_sequence():
    assert db._booking_row_sequence_value({"invoice_number": "INV25"}) == 25
    assert db._booking_row_sequence_value({"invoice_number": "25"}) == 25
    assert db._booking_row_sequence_value({"invoice_number": "INV-25"}) == 25
    assert db._booking_row_sequence_value({"invoice_number": "INV 25"}) == 25
    assert db._booking_row_sequence_value(
        {"id": 7, "invoice_number": "", "invoice_status": "AUTHORISED"}
    ) == 7
    assert db._booking_row_sequence_value(
        {"id": 9, "invoice_number": "", "status": "Confirmed"}
    ) == 0
    db.init_db()
    existing_id = _create_booking("Inv25Count")
    db.update_booking_invoice_fields(existing_id, {"invoice_number": "INV25"})
    with db.get_connection() as conn:
        assert db._max_used_invoice_sequence(conn) >= 25
    assert dict(db.get_booking(existing_id))["invoice_number"] == "INV25"
    return True


def test_next_number_continues_from_max_inv25_after_counter_reset():
    """Redeploy recreating invoice_sequence at 1 must still issue INV26 after INV25."""
    db.init_db()
    existing_id = _create_booking("ExistingInv25")
    db.update_booking_invoice_fields(existing_id, {"invoice_number": "INV25"})
    before = dict(db.get_booking(existing_id))
    _force_sequence(1)

    with patch("database._max_used_invoice_sequence", return_value=25):
        allocated = db.allocate_invoice_number()
    assert allocated == 26, allocated
    after = dict(db.get_booking(existing_id))
    assert after["invoice_number"] == before["invoice_number"] == "INV25"

    with db.get_connection() as conn:
        real_max = db._max_used_invoice_sequence(conn)
    unique = real_max + 100000
    db.update_booking_invoice_fields(
        existing_id, {"invoice_number": "INV{0}".format(unique)}
    )
    _force_sequence(1)
    allocated_real = db.allocate_invoice_number()
    assert allocated_real == unique + 1, (allocated_real, unique)

    new_id = _create_booking("NextAfterMax")
    assigned = invoice_numbering.ensure_booking_invoice_number(new_id)
    assert assigned == str(unique + 2)
    row = dict(db.get_booking(new_id))
    assert row["invoice_number"] == str(unique + 2)
    doc = invoice_pdf.build_invoice_document({**row, "extra_charges": []})
    expected = "INV{0}".format(unique + 2)
    assert doc["invoice_number"] == expected
    assert doc["bank"]["payment_reference"] == expected
    assert dict(db.get_booking(existing_id))["invoice_number"] == "INV{0}".format(
        unique
    )
    return True


def test_issued_invoice_without_stored_number_is_not_duplicated():
    db.init_db()
    issued_id = _create_booking("IssuedEmpty")
    db.update_booking_invoice_fields(
        issued_id,
        {"invoice_number": "", "invoice_status": "AUTHORISED"},
    )
    _force_sequence(1)
    allocated = db.allocate_invoice_number()
    assert allocated > issued_id
    assert dict(db.get_booking(issued_id))["invoice_number"] in ("", None)
    return True


def test_recreating_sequence_table_continues_from_db_max():
    """CREATE TABLE invoice_sequence at 1 (redeploy) must not issue INV1 when invoices exist."""
    db.init_db()
    existing_id = _create_booking("RecreateSeq")
    with db.get_connection() as conn:
        max_used = db._max_used_invoice_sequence(conn)
    unique = max(max_used, 25) + 200000
    db.update_booking_invoice_fields(
        existing_id, {"invoice_number": "INV{0}".format(unique)}
    )
    stored_before = dict(db.get_booking(existing_id))["invoice_number"]
    with db.get_connection() as conn:
        conn.execute("DROP TABLE invoice_sequence")
        conn.commit()
    allocated = db.allocate_invoice_number()
    assert allocated == unique + 1, allocated
    assert dict(db.get_booking(existing_id))["invoice_number"] == stored_before
    return True


def test_reassign_stray_inv1_when_max_is_45():
    """Product example: stray stored INV1 + max INV45 → INV46; next allocate is 47."""
    db.init_db()
    stray_id = _create_booking("StrayInv1")
    keep_id = _create_booking("KeepInv25")
    db.update_booking_invoice_fields(
        stray_id, {"invoice_number": "1", "hourly_rate": 199.0, "gst_enabled": 1}
    )
    db.update_booking_invoice_fields(keep_id, {"invoice_number": "INV25"})
    before_stray = dict(db.get_booking(stray_id))
    before_keep = dict(db.get_booking(keep_id))["invoice_number"]
    _force_sequence(46)

    with patch("database._max_used_invoice_sequence", return_value=45):
        dry = db.reassign_mistaken_invoice_one(dry_run=True)
        assert dry["target_id"] == stray_id
        assert dry["old_number"] == "1"
        assert dry["new_number"] == 46
        assert dry["changed"] is False
        assert dict(db.get_booking(stray_id))["invoice_number"] == "1"

        applied = db.reassign_mistaken_invoice_one(dry_run=False)

    assert applied["changed"] is True
    assert applied["target_id"] == stray_id
    assert applied["new_number"] == 46
    after_stray = dict(db.get_booking(stray_id))
    assert after_stray["invoice_number"] == "46"
    assert after_stray["hourly_rate"] == before_stray["hourly_rate"]
    assert after_stray["gst_enabled"] == before_stray["gst_enabled"]
    assert after_stray["customer_name"] == before_stray["customer_name"]
    assert after_stray["phone"] == before_stray["phone"]
    assert dict(db.get_booking(keep_id))["invoice_number"] == before_keep
    doc = invoice_pdf.build_invoice_document({**after_stray, "extra_charges": []})
    assert doc["invoice_number"] == "INV46"
    assert doc["bank"]["payment_reference"] == "INV46"

    again = db.reassign_mistaken_invoice_one(dry_run=False)
    assert again["changed"] is False
    assert dict(db.get_booking(stray_id))["invoice_number"] == "46"
    return True


def test_reassign_stray_inv1_aligns_sequence_to_next_unused():
    db.init_db()
    stray_id = _create_booking("StrayInv1Seq")
    current_id = _create_booking("CurrentMaxInv")
    other_id = _create_booking("KeepOtherInv")
    db.update_booking_invoice_fields(other_id, {"invoice_number": "12"})
    before_other = dict(db.get_booking(other_id))
    with db.get_connection() as conn:
        max_used = db._max_used_invoice_sequence(conn)
    unique_max = max(max_used, 45) + 400000
    db.update_booking_invoice_fields(current_id, {"invoice_number": str(unique_max)})
    db.update_booking_invoice_fields(stray_id, {"invoice_number": "INV1"})
    _force_sequence(unique_max + 1)

    report = db.reassign_mistaken_invoice_one()
    assert report["changed"] is True
    assert report["target_id"] == stray_id
    assert report["new_number"] == unique_max + 1
    assert dict(db.get_booking(stray_id))["invoice_number"] == str(unique_max + 1)
    assert dict(db.get_booking(current_id))["invoice_number"] == str(unique_max)
    assert dict(db.get_booking(other_id))["invoice_number"] == before_other["invoice_number"]
    assert dict(db.get_booking(other_id))["customer_name"] == before_other["customer_name"]

    with db.get_connection() as conn:
        seq = conn.execute(
            "SELECT next_number FROM invoice_sequence WHERE id = 1"
        ).fetchone()
    assert int(seq["next_number"]) >= unique_max + 2
    allocated = db.allocate_invoice_number()
    assert allocated == unique_max + 2
    return True


def test_reassign_does_not_rewrite_booking_id_1():
    db.init_db()
    row1 = db.get_booking(1)
    if not row1:
        return True
    original = dict(row1).get("invoice_number")
    db.update_booking_invoice_fields(1, {"invoice_number": "1"})
    other_id = _create_booking("NotInv1")
    with db.get_connection() as conn:
        max_used = db._max_used_invoice_sequence(conn)
    unique_max = max(max_used, 45) + 500000
    db.update_booking_invoice_fields(other_id, {"invoice_number": str(unique_max)})
    try:
        report = db.reassign_mistaken_invoice_one()
        assert dict(db.get_booking(1))["invoice_number"] == "1"
        assert report.get("target_id") != 1
        if report.get("changed"):
            assert report["target_id"] != 1
    finally:
        db.update_booking_invoice_fields(
            1, {"invoice_number": original if original is not None else ""}
        )
    return True


def main():
    tests = [
        ("first_and_second", test_first_and_second_invoice_numbers),
        ("edit_keeps_number", test_edit_keeps_same_number),
        ("pdf_number", test_pdf_shows_assigned_number),
        ("format_existing", test_format_existing_numeric_invoice),
        ("booking_id_fallback", test_booking_id_fallback_when_no_stored_number),
        ("preview_no_reference", test_invoice_preview_no_reference_field),
        ("payment_reference_match", test_payment_reference_matches_invoice_number),
        ("reference_25_fallback", test_reference_25_without_stored_invoice_number),
        ("sequence_reinit", test_sequence_survives_reinit),
        ("no_reuse_after_delete", test_deleted_invoice_does_not_reuse_number),
        ("inv25_counts_in_max", test_stored_inv25_counts_toward_max_sequence),
        ("continue_after_reset", test_next_number_continues_from_max_inv25_after_counter_reset),
        ("no_duplicate_issued_id", test_issued_invoice_without_stored_number_is_not_duplicated),
        ("recreate_sequence_table", test_recreating_sequence_table_continues_from_db_max),
        ("reassign_inv1_to_46", test_reassign_stray_inv1_when_max_is_45),
        ("reassign_inv1_sequence", test_reassign_stray_inv1_aligns_sequence_to_next_unused),
        ("reassign_skips_id_1", test_reassign_does_not_rewrite_booking_id_1),
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
