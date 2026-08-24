#!/usr/bin/env python3
"""E2E tests — bank transfer CSV matching marks invoices Paid when amounts match."""

import os
import sys
import time
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import bank_transfer_match
import database as db
import invoice
import invoice_numbering
from app import app
from integrations import invoice_pdf


_test_user_counter = 0
_test_booking_counter = 0
_invoice_counter = 0


def _ensure_schema():
    db.reset_init_db_for_tests()
    db.init_db()
    db.list_bank_transactions(limit=1)


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    _ensure_schema()
    uid = db.create_staff_user(
        "bank-match-{0}-{1}".format(os.getpid(), _test_user_counter),
        auth.hash_password("test"),
        "Bank Match Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _next_invoice_number():
    global _invoice_counter
    _invoice_counter += 1
    return str(910000000 + (os.getpid() % 10000) * 100 + _invoice_counter)


def _create_unpaid_invoice(invoice_number, total=500.0):
    global _test_booking_counter
    _test_booking_counter += 1
    marker = "BankMatch{0}-{1}-{2}".format(
        os.getpid(), _test_booking_counter, time.time_ns()
    )
    booking_id = db.create_booking(
        marker,
        "0412000111",
        "{0}@example.com".format(marker.lower()),
        "1 Bank St, Perth WA",
        "2 Bank Ave, Fremantle WA",
        "2026-11-15",
        2,
        "bank transfer match test {0}".format(marker),
        start_time="08:00",
        finish_time="09:00",
        duration_hours="1",
        hourly_rate=float(total),
        callout_fee=0.0,
        gst_enabled=0,
        payment_status="Unpaid",
        status="Invoiced",
    )
    db.update_booking_invoice_fields(booking_id, {"invoice_number": str(invoice_number)})
    row = dict(db.get_booking(booking_id))
    row["extra_charges"] = db.list_extra_charges(booking_id)
    actual_total = round(float(invoice.calculate_invoice_totals(row)["total"]), 2)
    assert actual_total == round(float(total), 2), (
        "Invoice total {0} != {1}".format(actual_total, total)
    )
    displayed = invoice_numbering.display_invoice_number(row)
    assert displayed == "INV{0}".format(int(invoice_number))
    assert bank_transfer_match.payment_reference_for_booking(row) == displayed
    return booking_id, displayed, actual_total, marker


def _csv_text(reference, amount, description):
    return (
        "Transaction date,Description,Reference,Amount\n"
        "2026-08-20,{0},{1},{2:.2f}\n".format(description, reference, float(amount))
    )


def _post_csv(client, csv_text):
    return client.post(
        "/settings/bank-transfers",
        data={"csv_file": (BytesIO(csv_text.encode("utf-8")), "bank.csv")},
        follow_redirects=True,
    )


def test_inv25_token_is_payment_reference():
    booking = {"id": 99, "invoice_number": "25"}
    assert invoice_numbering.format_invoice_number("25") == "INV25"
    assert invoice_numbering.display_invoice_number(booking) == "INV25"
    assert bank_transfer_match.payment_reference_for_booking(booking) == "INV25"
    assert bank_transfer_match.extract_invoice_tokens("INV25") == ["INV25"]
    assert bank_transfer_match.extract_invoice_tokens("Inv25") == ["INV25"]
    assert bank_transfer_match.extract_invoice_tokens("inv25") == ["INV25"]
    assert bank_transfer_match.extract_invoice_tokens("INV 25") == ["INV25"]
    assert bank_transfer_match.extract_invoice_tokens("Inv 25") == ["INV25"]
    assert bank_transfer_match.extract_invoice_tokens("Payment INV25 received") == [
        "INV25"
    ]
    assert bank_transfer_match.extract_invoice_tokens("INV33 INV33") == ["INV33"]
    doc = invoice_pdf.build_invoice_document({**booking, "extra_charges": []})
    assert doc["invoice_number"] == "INV25"
    assert doc["bank"]["payment_reference"] == "INV25"
    return True


def test_csv_parses_required_columns():
    rows = bank_transfer_match.parse_bank_csv(
        _csv_text("INV25", 500.00, "Payment received")
    )
    assert len(rows) == 1
    assert rows[0]["transaction_date"] == "2026-08-20"
    assert rows[0]["description"] == "Payment received"
    assert rows[0]["reference"] == "INV25"
    assert rows[0]["amount"] == 500.00
    return True


def test_inv25_500_marks_paid_and_completed():
    """INV25 / $500.00 match → Invoice Paid and Booking Completed."""
    client = _login_client()
    number = "25"
    existing = db.find_bookings_by_invoice_display("INV25")
    if existing:
        number = _next_invoice_number()
    booking_id, displayed, total, marker = _create_unpaid_invoice(number, 500.00)
    control_id, _c_disp, _c_total, _c_marker = _create_unpaid_invoice(
        _next_invoice_number(), 500.00
    )
    control_before = dict(db.get_booking(control_id))
    desc = "Payment received {0}".format(marker)
    resp = _post_csv(client, _csv_text(displayed, 500.00, desc))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert "1 paid" in html or "paid 1" in html or "1 paid" in html.lower() or "paid" in html.lower()
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Completed"
    assert row["customer_name"] == marker
    control_after = dict(db.get_booking(control_id))
    assert control_after["payment_status"] == control_before["payment_status"] == "Unpaid"
    assert control_after["status"] == control_before["status"] == "Invoiced"
    assert displayed.startswith("INV")
    return displayed, booking_id


def test_inv25_400_is_payment_mismatch():
    """INV25 / $400.00 against $500.00 invoice → not Paid, Dashboard warning."""
    client = _login_client()
    booking_id, displayed, total, marker = _create_unpaid_invoice(
        _next_invoice_number(), 500.00
    )
    desc = "Mismatch payment {0}".format(marker)
    resp = _post_csv(client, _csv_text(displayed, 400.00, desc))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Unpaid"
    assert row["status"] == "Invoiced"
    dash = client.get("/dashboard").get_data(as_text=True)
    assert "Payment mismatch" in dash
    assert displayed in dash
    assert "$400.00" in dash
    assert "$500.00" in dash
    mismatches = db.list_bank_transactions(match_status="mismatch", limit=50)
    tokens = [str(item.get("invoice_token") or "") for item in mismatches]
    assert displayed in tokens
    return displayed, booking_id


def test_duplicate_csv_row_is_not_processed_twice():
    client = _login_client()
    booking_id, displayed, total, marker = _create_unpaid_invoice(
        _next_invoice_number(), 500.00
    )
    desc = "Duplicate payment {0}".format(marker)
    csv_text = _csv_text(displayed, 500.00, desc)
    first = bank_transfer_match.import_bank_transactions(
        bank_transfer_match.parse_bank_csv(csv_text)
    )
    assert first["imported"] == 1
    assert first["paid"] == 1
    assert first["skipped"] == 0
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Completed"
    paid_at = row.get("paid_at")
    second = bank_transfer_match.import_bank_transactions(
        bank_transfer_match.parse_bank_csv(csv_text)
    )
    assert second["imported"] == 0
    assert second["skipped"] == 1
    assert second["paid"] == 0
    again = dict(db.get_booking(booking_id))
    assert again["payment_status"] == "Paid"
    assert again["status"] == "Completed"
    assert again.get("paid_at") == paid_at
    return True


def test_mismatch_then_matching_amount_pays():
    """$400 mismatch stays unpaid; later $500 match marks Paid + Completed."""
    _ensure_schema()
    booking_id, displayed, total, marker = _create_unpaid_invoice(
        _next_invoice_number(), 500.00
    )
    mismatch_desc = "Short payment {0}".format(marker)
    match_desc = "Balance payment {0}".format(marker)
    short = bank_transfer_match.import_bank_transactions(
        bank_transfer_match.parse_bank_csv(_csv_text(displayed, 400.00, mismatch_desc))
    )
    assert short["mismatches"] == 1
    assert short["paid"] == 0
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Unpaid"
    assert row["status"] == "Invoiced"
    matched = bank_transfer_match.import_bank_transactions(
        bank_transfer_match.parse_bank_csv(_csv_text(displayed, 500.00, match_desc))
    )
    assert matched["paid"] == 1
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Completed"
    mismatches = db.list_bank_transactions(match_status="mismatch", limit=50)
    assert any(
        str(item.get("invoice_token") or "") == displayed
        and abs(float(item["amount"]) - 400.00) < 0.005
        for item in mismatches
    )
    return True


def test_settings_and_bank_transfers_pages():
    client = _login_client()
    settings_html = client.get("/settings").get_data(as_text=True)
    assert "Bank transfers" in settings_html
    assert "/settings/bank-transfers" in settings_html
    page = client.get("/settings/bank-transfers")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Import CSV" in html
    assert "Transaction date" in html
    desktop_nav = html.split("main-nav-desktop", 1)[-1].split("</nav>", 1)[0]
    assert ">Driver<" not in desktop_nav
    assert ">Invoices<" not in desktop_nav
    return True


def test_unauthenticated_bank_transfers_redirects():
    client = app.test_client()
    resp = client.get("/settings/bank-transfers", follow_redirects=False)
    assert resp.status_code in (302, 303)
    return True


WESTPAC_EXAMPLES = [
    "* DEPOSIT-OSKO PAYMENT 2217304 Prava Timilsina INV24",
    "* DEPOSIT-OSKO PAYMENT 2478731 ALEISHA VO INV26",
    "* DEPOSIT-OSKO PAYMENT 2986170 COBY GODWIN Japanese removals Inv 32 godwin",
    "* DEPOSIT DENISE LICKFOLD Lickfold Inv25",
    "* DEPOSIT-OSKO PAYMENT 2842555 JOANNA NG INV33 INV33",
    "* DEPOSIT-OSKO PAYMENT 2313852 MR SEUNG HUN BAEK INV27 INV27",
]


def test_westpac_description_tokens():
    expected = ["INV24", "INV26", "INV32", "INV25", "INV33", "INV27"]
    for narrative, token in zip(WESTPAC_EXAMPLES, expected):
        got = bank_transfer_match.extract_invoice_tokens(narrative)
        assert got == [token], "{0} -> {1}".format(narrative, got)
        empty_ref = bank_transfer_match.extract_invoice_tokens(
            bank_transfer_match.invoice_search_text(
                {"reference": "", "description": narrative}
            )
        )
        assert empty_ref == [token]
    return True


def _westpac_csv(rows):
    """rows: list of (date_ddmmyyyy, narrative, debit, credit)."""
    lines = [
        "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance,Categories,Serial"
    ]
    for date_text, narrative, debit, credit in rows:
        lines.append(
            "032-000 123456,{0},{1},{2},{3},1000.00,,".format(
                date_text, narrative, debit, credit
            )
        )
    return "\n".join(lines) + "\n"


def test_westpac_csv_pays_from_narrative():
    """Westpac Credit in Narrative INV* with matching amount → Paid + Completed."""
    client = _login_client()
    booking_id, displayed, total, marker = _create_unpaid_invoice(
        _next_invoice_number(), 500.00
    )
    control_id, _c_disp, _c_total, _c_marker = _create_unpaid_invoice(
        _next_invoice_number(), 500.00
    )
    control_before = dict(db.get_booking(control_id))
    number = displayed.replace("INV", "")
    narrative = (
        "* DEPOSIT-OSKO PAYMENT 2217304 Prava Timilsina INV{0}".format(number)
    )
    csv_text = _westpac_csv(
        [("20/08/2026", narrative, "", "500.00")]
    )
    parsed = bank_transfer_match.parse_bank_csv(csv_text)
    assert len(parsed) == 1
    assert parsed[0]["reference"] == ""
    assert parsed[0]["description"] == narrative
    assert parsed[0]["amount"] == 500.00
    assert parsed[0]["transaction_date"] == "2026-08-20"
    resp = _post_csv(client, csv_text)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Completed"
    control_after = dict(db.get_booking(control_id))
    assert control_after["payment_status"] == control_before["payment_status"]
    assert control_after["status"] == control_before["status"]
    return displayed, booking_id


def test_westpac_inv_space_and_duplicate_token():
    _ensure_schema()
    booking_a, disp_a, _t, _m = _create_unpaid_invoice(_next_invoice_number(), 500.00)
    booking_b, disp_b, _t2, _m2 = _create_unpaid_invoice(_next_invoice_number(), 500.00)
    num_a = disp_a.replace("INV", "")
    num_b = disp_b.replace("INV", "")
    csv_text = _westpac_csv(
        [
            (
                "21/08/2026",
                "* DEPOSIT-OSKO PAYMENT 2986170 COBY GODWIN Japanese removals Inv {0} godwin".format(
                    num_a
                ),
                "",
                "500.00",
            ),
            (
                "22/08/2026",
                "* DEPOSIT-OSKO PAYMENT 2842555 JOANNA NG INV{0} INV{0}".format(num_b),
                "",
                "500.00",
            ),
        ]
    )
    summary = bank_transfer_match.import_bank_transactions(
        bank_transfer_match.parse_bank_csv(csv_text)
    )
    assert summary["imported"] == 2
    assert summary["paid"] == 2
    assert summary["unmatched"] == 0
    assert dict(db.get_booking(booking_a))["payment_status"] == "Paid"
    assert dict(db.get_booking(booking_a))["status"] == "Completed"
    assert dict(db.get_booking(booking_b))["payment_status"] == "Paid"
    assert dict(db.get_booking(booking_b))["status"] == "Completed"
    return True


def test_westpac_mismatch_and_negative_debit():
    _ensure_schema()
    booking_id, displayed, _t, _m = _create_unpaid_invoice(
        _next_invoice_number(), 500.00
    )
    number = displayed.replace("INV", "")
    csv_text = _westpac_csv(
        [
            (
                "22/08/2026",
                "* DEPOSIT-OSKO PAYMENT 2313852 MR SEUNG HUN BAEK INV{0} INV{0}".format(
                    number
                ),
                "",
                "400.00",
            ),
            (
                "23/08/2026",
                "EFTPOS MERCHANT WESTPAC CARD PURCHASE",
                "25.00",
                "",
            ),
            (
                "23/08/2026",
                "SALARY PAYMENT NO INVOICE TOKEN",
                "",
                "120.00",
            ),
        ]
    )
    summary = bank_transfer_match.import_bank_transactions(
        bank_transfer_match.parse_bank_csv(csv_text)
    )
    assert summary["mismatches"] == 1
    assert summary["paid"] == 0
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Unpaid"
    assert row["status"] == "Invoiced"
    statuses = [item["match_status"] for item in summary["results"]]
    assert "mismatch" in statuses
    assert "skipped" in statuses
    assert "unmatched" in statuses
    return True


def test_rematch_unmatched_westpac_rows_in_place():
    """Previously unmatched Description rows can be re-matched without delete."""
    _ensure_schema()
    booking_id, displayed, _t, marker = _create_unpaid_invoice(
        _next_invoice_number(), 500.00
    )
    control_id, _c, _ct, _cm = _create_unpaid_invoice(_next_invoice_number(), 500.00)
    control_before = dict(db.get_booking(control_id))
    number = displayed.replace("INV", "")
    description = (
        "* DEPOSIT DENISE LICKFOLD Lickfold Inv{0}".format(number)
    )
    parsed = {
        "transaction_date": "2026-08-21",
        "description": description,
        "reference": "",
        "amount": 500.00,
    }
    fingerprint = bank_transfer_match.fingerprint_for(
        parsed["transaction_date"],
        parsed["description"],
        parsed["reference"],
        parsed["amount"],
    )
    txn_id = db.insert_bank_transaction(
        {
            "fingerprint": fingerprint,
            "transaction_date": parsed["transaction_date"],
            "description": parsed["description"],
            "reference": parsed["reference"],
            "amount": parsed["amount"],
            "match_status": "unmatched",
            "message": "No invoice number in Reference.",
        }
    )
    before_count = db.count_bank_transactions()
    assert dict(db.get_booking(booking_id))["payment_status"] == "Unpaid"

    summary = bank_transfer_match.rematch_unmatched_transactions()
    assert summary["rematched"] >= 1
    assert summary["paid"] >= 1
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Completed"
    stored = db.get_bank_transaction_by_fingerprint(fingerprint)
    assert stored is not None
    assert int(stored["id"]) == int(txn_id)
    assert stored["match_status"] == "paid"
    assert stored["invoice_token"] == displayed
    assert db.count_bank_transactions() == before_count
    control_after = dict(db.get_booking(control_id))
    assert control_after["payment_status"] == control_before["payment_status"]
    assert control_after["status"] == control_before["status"]

    again = bank_transfer_match.import_bank_transactions([parsed])
    assert again["skipped"] == 1
    assert again["imported"] == 0
    assert dict(db.get_booking(booking_id)).get("payment_status") == "Paid"
    assert db.count_bank_transactions() == before_count
    return True


def test_reimport_rematches_unmatched_without_duplicate_row():
    _ensure_schema()
    booking_id, displayed, _t, _m = _create_unpaid_invoice(
        _next_invoice_number(), 500.00
    )
    number = displayed.replace("INV", "")
    narrative = "* DEPOSIT-OSKO PAYMENT 2478731 ALEISHA VO INV{0}".format(number)
    parsed = {
        "transaction_date": "2026-08-20",
        "description": narrative,
        "reference": "",
        "amount": 500.00,
    }
    fingerprint = bank_transfer_match.fingerprint_for(
        parsed["transaction_date"],
        parsed["description"],
        parsed["reference"],
        parsed["amount"],
    )
    txn_id = db.insert_bank_transaction(
        {
            "fingerprint": fingerprint,
            "transaction_date": parsed["transaction_date"],
            "description": parsed["description"],
            "reference": parsed["reference"],
            "amount": parsed["amount"],
            "match_status": "unmatched",
            "message": "No invoice number in Reference.",
        }
    )
    csv_text = _westpac_csv([("20/08/2026", narrative, "", "500.00")])
    summary = bank_transfer_match.import_bank_transactions(
        bank_transfer_match.parse_bank_csv(csv_text)
    )
    assert summary["imported"] == 0
    assert summary["rematched"] == 1
    assert summary["paid"] == 1
    stored = db.get_bank_transaction_by_fingerprint(fingerprint)
    assert int(stored["id"]) == int(txn_id)
    assert stored["match_status"] == "paid"
    assert dict(db.get_booking(booking_id))["payment_status"] == "Paid"
    assert dict(db.get_booking(booking_id))["status"] == "Completed"
    return True


def test_rematch_button_on_bank_transfers_page():
    client = _login_client()
    booking_id, displayed, _t, _m = _create_unpaid_invoice(
        _next_invoice_number(), 500.00
    )
    number = displayed.replace("INV", "")
    description = "* DEPOSIT-OSKO PAYMENT 2313852 MR SEUNG HUN BAEK INV{0} INV{0}".format(
        number
    )
    parsed = {
        "transaction_date": "2026-08-22",
        "description": description,
        "reference": "",
        "amount": 500.00,
    }
    db.insert_bank_transaction(
        {
            "fingerprint": bank_transfer_match.fingerprint_for(
                parsed["transaction_date"],
                parsed["description"],
                parsed["reference"],
                parsed["amount"],
            ),
            **parsed,
            "match_status": "unmatched",
            "message": "No invoice number in Reference.",
        }
    )
    page = client.get("/settings/bank-transfers")
    html = page.get_data(as_text=True)
    assert "Re-match unmatched" in html
    resp = client.post(
        "/settings/bank-transfers",
        data={"action": "rematch_unmatched"},
        follow_redirects=True,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert "Re-matched" in resp.get_data(as_text=True)
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Completed"
    return True


def main():
    tests = [
        ("inv25_token_payment_reference", test_inv25_token_is_payment_reference),
        ("csv_required_columns", test_csv_parses_required_columns),
        ("inv25_500_paid_completed", test_inv25_500_marks_paid_and_completed),
        ("inv25_400_payment_mismatch", test_inv25_400_is_payment_mismatch),
        ("duplicate_row_skipped", test_duplicate_csv_row_is_not_processed_twice),
        ("mismatch_then_match", test_mismatch_then_matching_amount_pays),
        ("settings_bank_transfers_page", test_settings_and_bank_transfers_pages),
        ("unauthenticated_redirect", test_unauthenticated_bank_transfers_redirects),
        ("westpac_description_tokens", test_westpac_description_tokens),
        ("westpac_csv_pays_from_narrative", test_westpac_csv_pays_from_narrative),
        ("westpac_inv_space_and_duplicate", test_westpac_inv_space_and_duplicate_token),
        ("westpac_mismatch_and_negative", test_westpac_mismatch_and_negative_debit),
        ("rematch_unmatched_in_place", test_rematch_unmatched_westpac_rows_in_place),
        ("reimport_rematches_unmatched", test_reimport_rematches_unmatched_without_duplicate_row),
        ("rematch_button", test_rematch_button_on_bank_transfers_page),
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
