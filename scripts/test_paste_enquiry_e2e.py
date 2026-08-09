#!/usr/bin/env python3
"""Tests for paste-and-analyse customer enquiry parsing."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from integrations import enquiry_parser


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
    assert "0412" in parsed["phone"]
    assert "Cannington" in parsed["pickup_address"]
    assert "Innaloo" in parsed["delivery_address"]
    assert parsed["move_date"] == "2026-08-15"
    assert parsed["start_time"] == "08:00"
    return True


def test_summary_rows_match_ui_labels():
    parsed = enquiry_parser.parse_pasted_text(SAMPLE_TEXT)
    rows = dict(enquiry_parser.summary_rows(parsed))
    assert rows["Name"] == "John Smith"
    assert rows["Phone"].startswith("0412")
    assert rows["From"] != "—"
    assert rows["To"] != "—"
    assert rows["Date"] == "15/08/2026"
    assert rows["Start Time"] == "8:00 AM"
    return True


def test_apply_parsed_fields_prefills_form():
    parsed = enquiry_parser.parse_pasted_text(SAMPLE_TEXT)
    form = enquiry_parser.apply_parsed_fields(
        {"start_time": "09:00", "phone": "", "email": ""},
        parsed,
    )
    assert form["customer_name"] == "John Smith"
    assert form["start_time"] == "08:00"
    assert form["move_date"] == "2026-08-15"
    return True


def test_empty_paste_returns_blank_fields():
    parsed = enquiry_parser.parse_pasted_text("")
    assert not parsed["customer_name"]
    assert parsed["confidence"] == 0.0
    return True


def main():
    tests = [
        test_sample_message_extracts_core_fields,
        test_summary_rows_match_ui_labels,
        test_apply_parsed_fields_prefills_form,
        test_empty_paste_returns_blank_fields,
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
