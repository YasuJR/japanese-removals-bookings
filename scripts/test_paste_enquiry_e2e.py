#!/usr/bin/env python3
"""Tests for paste-and-analyse customer enquiry parsing."""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from integrations import enquiry_parser

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

SAMPLE_TEXT = """Hi Yasu, this is John Smith
0412 345 678
10 ABC St, Cannington
moving to 25 XYZ Rd, Innaloo
15/08/2026
Start time 8:00 AM
Need help with packing"""


def test_sample_message_extracts_core_fields():
    parsed = enquiry_parser.parse_pasted_text(SAMPLE_TEXT)
    assert parsed["customer_name"] == "John Smith"
    assert parsed["phone"] == "0412 345 678"
    assert parsed["pickup_address"] == "10 ABC St, Cannington"
    assert parsed["delivery_address"] == "25 XYZ Rd, Innaloo"
    assert parsed["move_date"] == "2026-08-15"
    assert parsed["start_time"] == "08:00"
    assert parsed["notes"] == "Need help with packing"
    return True


def test_summary_rows_match_ui_labels():
    parsed = enquiry_parser.parse_pasted_text(SAMPLE_TEXT)
    rows = dict(enquiry_parser.summary_rows(parsed))
    assert rows["Name"] == "John Smith"
    assert rows["Phone"] == "0412 345 678"
    assert rows["From"] == "10 ABC St, Cannington"
    assert rows["To"] == "25 XYZ Rd, Innaloo"
    assert rows["Date"] == "15/08/2026"
    assert rows["Start Time"] == "8:00 AM"
    assert rows["Notes"] == "Need help with packing"
    return True


def test_apply_parsed_fields_prefills_form():
    parsed = enquiry_parser.parse_pasted_text(SAMPLE_TEXT)
    form = enquiry_parser.apply_parsed_fields(
        {"start_time": "09:00", "phone": "0481 089 573", "email": "info@example.com"},
        parsed,
    )
    assert form["customer_name"] == "John Smith"
    assert form["phone"] == "0412 345 678"
    assert form["email"] == ""
    assert form["start_time"] == "08:00"
    assert form["move_date"] == "2026-08-15"
    assert form["notes"] == "Need help with packing"
    return True


def test_empty_paste_returns_blank_fields():
    parsed = enquiry_parser.parse_pasted_text("")
    assert not parsed["customer_name"]
    assert parsed["confidence"] == 0.0
    return True


def test_analyse_button_skips_html5_validation():
    template = (ROOT / "templates" / "_paste_enquiry_panel.html").read_text()
    assert 'value="analyse_paste"' in template
    assert "formnovalidate" in template
    return True


def test_analyse_paste_prefills_new_booking_form():
    import auth
    import database as db
    from app import app

    db.init_db()
    uid = db.create_staff_user(
        "paste-test-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Paste Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "paste-test"

    resp = client.post(
        "/bookings/new",
        data={"action": "analyse_paste", "paste_text": SAMPLE_TEXT},
    )
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)

    expected = {
        "customer_name": "John Smith",
        "phone": "0412 345 678",
        "pickup_address": "10 ABC St, Cannington",
        "delivery_address": "25 XYZ Rd, Innaloo",
        "move_date": "2026-08-15",
        "start_time": "08:00",
    }
    for name, value in expected.items():
        match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', html)
        assert match, "Missing input for {0}".format(name)
        assert match.group(1) == value, "{0} expected {1!r}, got {2!r}".format(
            name, value, match.group(1)
        )

    notes = re.search(r'name="notes"[^>]*>([^<]*)', html, re.S)
    assert notes and notes.group(1).strip() == "Need help with packing"
    assert "Extracted details" in html
    return True


def main():
    tests = [
        test_sample_message_extracts_core_fields,
        test_summary_rows_match_ui_labels,
        test_apply_parsed_fields_prefills_form,
        test_empty_paste_returns_blank_fields,
        test_analyse_button_skips_html5_validation,
        test_analyse_paste_prefills_new_booking_form,
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
