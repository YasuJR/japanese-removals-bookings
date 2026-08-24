#!/usr/bin/env python3
"""Tests for paste-and-analyse customer enquiry parsing."""

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from integrations import enquiry_parser

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

REFERENCE = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo("Australia/Perth"))

SAMPLE_TEXT = """Hi Yasu, this is John Smith
0412 345 678
10 ABC St, Cannington
moving to 25 XYZ Rd, Innaloo
15/08/2026
Start time 8:00 AM
Need help with packing"""

TEST_MESSAGES = {
    "TEST 1": """Hi mate, Steve here. Looking to move next Friday from Cannington to Innaloo. My number is 0412345678. Probably around 9 if possible.""",
    "TEST 2": """Hi Yasu
My name is Sarah Brown
Pickup: 15 Albany Hwy, Victoria Park
Drop off: 28 Scarborough Beach Rd, North Perth
Mobile +61 412 987 654
15 August at 8am
There are stairs at pickup and a piano.""",
    "TEST 3": """Moving tomorrow morning
John
0413 555 666
from Morley to Balcatta
Need packing help.""",
    "TEST 4": """Hello, can you help with a move on Monday?
10 Smith Street, Cannington to 5 Jones Road, Como
Email: test@example.com
Phone: 0400111222
Thanks, Michael""",
    "TEST 5": """Hi, moving from Cannington to Innaloo sometime next week. Please call me on 0412 111 222.""",
}

LABELLED_NAME_DUAL_ADDRESS = """First Name: Prava
Last Name: Timilsina
Email: tprava@hotmail.com
Phone Number: 04333911541
6 Morgan Street, Shenton Park and the new address is
128A Broome Street, Cottesloe"""

MOVING_TO_DUAL_ADDRESS = """Name: Alex Chen
0412 999 888
12 King Street, Fremantle moving to 45 Beach Road, Scarborough"""

DESTINATION_IS_DUAL_ADDRESS = """Contact: Jamie Lee
0400 222 333
3/88 Hay Street, Subiaco and destination is
Unit 7, 19 Park Avenue, Nedlands WA 6009"""

NEW_PLACE_DUAL_ADDRESS = """Hi there
Sam Taylor
0412 345 678
22 River Road, Applecross — new place is 5 Ocean Drive, Cottesloe"""

NOTES_ONLY_EXTRA = """First Name: Casey
Last Name: Nguyen
Phone: 0411 222 333
Email: casey@example.com
7 Short Street, Belmont and the new address is
14 Long Avenue, Morley
Please bring extra boxes and a dolly."""


def _assert_notes_exclude_structured(parsed, *forbidden):
    notes = (parsed.get("notes") or "").lower()
    for fragment in forbidden:
        assert fragment.lower() not in notes, "notes must not contain {0!r}: {1!r}".format(
            fragment, parsed.get("notes")
        )


def test_labelled_first_last_name_and_new_address_is():
    parsed = _parse(LABELLED_NAME_DUAL_ADDRESS)
    assert parsed["customer_name"] == "Prava Timilsina"
    assert parsed["phone"] == "0433 911 541"
    assert parsed["email"] == "tprava@hotmail.com"
    assert parsed["pickup_address"] == "6 Morgan Street, Shenton Park"
    assert parsed["delivery_address"] == "128A Broome Street, Cottesloe"
    assert parsed["notes"] == ""
    assert "is" not in (parsed["delivery_address"] or "").lower().split()
    return True


def test_moving_to_dual_street_addresses():
    parsed = _parse(MOVING_TO_DUAL_ADDRESS)
    assert parsed["customer_name"] == "Alex Chen"
    assert parsed["pickup_address"] == "12 King Street, Fremantle"
    assert parsed["delivery_address"] == "45 Beach Road, Scarborough"
    _assert_notes_exclude_structured(parsed, "Alex Chen", "King Street", "Beach Road")
    return True


def test_destination_is_dual_street_addresses():
    parsed = _parse(DESTINATION_IS_DUAL_ADDRESS)
    assert parsed["customer_name"] == "Jamie Lee"
    assert parsed["pickup_address"] == "3/88 Hay Street, Subiaco"
    assert parsed["delivery_address"] == "Unit 7, 19 Park Avenue, Nedlands WA 6009"
    _assert_notes_exclude_structured(parsed, "Jamie Lee", "Hay Street", "Park Avenue")
    return True


def test_new_place_is_dual_street_addresses():
    parsed = _parse(NEW_PLACE_DUAL_ADDRESS)
    assert parsed["customer_name"] == "Sam Taylor"
    assert parsed["phone"] == "0412 345 678"
    assert parsed["pickup_address"] == "22 River Road, Applecross"
    assert parsed["delivery_address"] == "5 Ocean Drive, Cottesloe"
    _assert_notes_exclude_structured(parsed, "Sam Taylor", "River Road", "Ocean Drive")
    return True


def test_structured_fields_not_in_notes():
    parsed = _parse(NOTES_ONLY_EXTRA)
    assert parsed["customer_name"] == "Casey Nguyen"
    assert parsed["pickup_address"] == "7 Short Street, Belmont"
    assert parsed["delivery_address"] == "14 Long Avenue, Morley"
    assert "boxes" in parsed["notes"].lower()
    assert "dolly" in parsed["notes"].lower()
    _assert_notes_exclude_structured(
        parsed,
        "Casey",
        "Nguyen",
        "Short Street",
        "Long Avenue",
        "First Name",
        "Last Name",
    )
    return True


def test_invalid_tokens_never_used_as_addresses():
    parsed = _parse(LABELLED_NAME_DUAL_ADDRESS)
    for field in ("pickup_address", "delivery_address"):
        value = (parsed.get(field) or "").strip().lower()
        assert value not in {"is", "to", "and", "from"}, field
    return True


KERRIE_MATTHEWS_TEXT = """Kerrie Matthews
+61459840022
Pick up is 31 Irvine st, Peppermint Grove (you moved us on here in October last year)
Destination is 54 Webster St, Nedlands"""

VARIANT_ONE_LINE = (
    "John Smith 0412345678 Pickup: 10 King St, Perth Drop off: 25 Queen St, Subiaco"
)
VARIANT_MOVING_FROM = (
    "Sarah Lee Moving from 15 James Rd, Cannington to 80 Hay St, Perth Phone 0433 222 111"
)
VARIANT_AROUND_NINE = (
    "Tom Brown - 0400 111 222 Pick up from 12 Murray St, Como "
    "Deliver to 20 Oxford St, Leederville around 9am"
)


def test_kerrie_matthews_pick_up_is_and_destination_is():
    parsed = _parse(KERRIE_MATTHEWS_TEXT)
    assert parsed["customer_name"] == "Kerrie Matthews"
    assert parsed["customer_name"].lower() != "us on"
    assert parsed["phone"] == "0459 840 022"
    assert parsed["email"] == ""
    assert parsed["pickup_address"] == "31 Irvine St, Peppermint Grove"
    assert parsed["delivery_address"] == "54 Webster St, Nedlands"
    assert not parsed["delivery_address"].lower().startswith("is ")
    assert parsed["start_time"] == ""
    assert parsed["move_date"] == ""
    assert "october last year" in parsed["notes"].lower()
    _assert_notes_exclude_structured(
        parsed,
        "Kerrie Matthews",
        "31 Irvine",
        "54 Webster",
        "0459",
    )
    form = enquiry_parser.apply_parsed_fields(
        {
            "start_time": "08:00",
            "customer_name": "",
            "pickup_address": "",
            "delivery_address": "",
        },
        parsed,
    )
    assert form["customer_name"] == "Kerrie Matthews"
    assert form["phone"] == "0459 840 022"
    assert form["pickup_address"] == "31 Irvine St, Peppermint Grove"
    assert form["delivery_address"] == "54 Webster St, Nedlands"
    assert form["start_time"] == ""
    assert "october last year" in form["notes"].lower()
    rows = dict(enquiry_parser.summary_rows(parsed))
    assert rows["Name"] == "Kerrie Matthews"
    assert rows["From"] == "31 Irvine St, Peppermint Grove"
    assert rows["To"] == "54 Webster St, Nedlands"
    assert rows["Start Time"] == "—"
    assert rows["Date"] == "—"
    return True


def test_us_on_here_is_never_used_as_customer_name():
    parsed = _parse(KERRIE_MATTHEWS_TEXT)
    assert "us on" not in (parsed["customer_name"] or "").lower()
    parsed_here = _parse("Please quote (you moved us on here last year)")
    assert (parsed_here["customer_name"] or "").lower() not in {"us on", "us", "you"}
    return True


def test_one_line_pickup_and_drop_off_labels():
    parsed = _parse(VARIANT_ONE_LINE)
    assert parsed["customer_name"] == "John Smith"
    assert parsed["phone"] == "0412 345 678"
    assert parsed["pickup_address"] == "10 King St, Perth"
    assert parsed["delivery_address"] == "25 Queen St, Subiaco"
    assert parsed["start_time"] == ""
    return True


def test_moving_from_to_with_phone_on_same_line():
    parsed = _parse(VARIANT_MOVING_FROM)
    assert parsed["customer_name"] == "Sarah Lee"
    assert parsed["phone"] == "0433 222 111"
    assert parsed["pickup_address"] == "15 James Rd, Cannington"
    assert parsed["delivery_address"] == "80 Hay St, Perth"
    assert parsed["start_time"] == ""
    assert parsed["email"] == ""
    return True


def test_pick_up_from_deliver_to_around_9am():
    parsed = _parse(VARIANT_AROUND_NINE)
    assert parsed["customer_name"] == "Tom Brown"
    assert parsed["phone"] == "0400 111 222"
    assert parsed["pickup_address"] == "12 Murray St, Como"
    assert parsed["delivery_address"] == "20 Oxford St, Leederville"
    assert parsed["start_time"] == "09:00"
    assert "Time is approximate" in parsed["warnings"]
    return True


def test_analyse_paste_prefills_kerrie_matthews_form():
    import auth
    import database as db
    from app import app

    db.init_db()
    uid = db.create_staff_user(
        "kerrie-analyse-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Kerrie Analyse",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "kerrie-analyse"

    resp = client.post(
        "/bookings/new",
        data={"action": "analyse_paste", "paste_text": KERRIE_MATTHEWS_TEXT},
    )
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    expected = {
        "customer_name": "Kerrie Matthews",
        "phone": "0459 840 022",
        "pickup_address": "31 Irvine St, Peppermint Grove",
        "delivery_address": "54 Webster St, Nedlands",
        "move_date": "",
        "start_time": "",
        "email": "",
    }
    for name, value in expected.items():
        match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', html)
        assert match, "Missing input for {0}".format(name)
        assert match.group(1) == value, "{0} expected {1!r}, got {2!r}".format(
            name, value, match.group(1)
        )
    notes = re.search(r'name="notes"[^>]*>([^<]*)', html, re.S)
    assert notes and "october last year" in notes.group(1).lower()
    assert "Kerrie Matthews" in html
    name_value = re.search(r'name="customer_name"[^>]*value="([^"]*)"', html)
    assert name_value and name_value.group(1) == "Kerrie Matthews"
    return True


def _parse(text):
    return enquiry_parser.parse_pasted_text(text, reference=REFERENCE)


def test_sample_message_extracts_core_fields():
    parsed = _parse(SAMPLE_TEXT)
    assert parsed["customer_name"] == "John Smith"
    assert parsed["phone"] == "0412 345 678"
    assert parsed["pickup_address"] == "10 ABC St, Cannington"
    assert parsed["delivery_address"] == "25 XYZ Rd, Innaloo"
    assert parsed["move_date"] == "2026-08-15"
    assert parsed["start_time"] == "08:00"
    assert "packing" in parsed["notes"].lower()
    return True


def test_summary_rows_match_ui_labels():
    parsed = _parse(SAMPLE_TEXT)
    rows = dict(enquiry_parser.summary_rows(parsed))
    assert rows["Name"] == "John Smith"
    assert rows["Phone"] == "0412 345 678"
    assert rows["From"] == "10 ABC St, Cannington"
    assert rows["To"] == "25 XYZ Rd, Innaloo"
    assert rows["Date"] == "15/08/2026"
    assert rows["Start Time"] == "8:00 AM"
    assert "packing" in rows["Notes"].lower()
    return True


def test_apply_parsed_fields_prefills_form():
    parsed = _parse(SAMPLE_TEXT)
    form = enquiry_parser.apply_parsed_fields(
        {"start_time": "09:00", "phone": "0481 089 573", "email": "info@example.com"},
        parsed,
    )
    assert form["customer_name"] == "John Smith"
    assert form["phone"] == "0412 345 678"
    assert form["email"] == ""
    assert form["start_time"] == "08:00"
    assert form["move_date"] == "2026-08-15"
    assert "packing" in form["notes"].lower()
    return True


def test_empty_paste_returns_blank_fields():
    parsed = _parse("")
    assert not parsed["customer_name"]
    assert parsed["confidence"] == 0.0
    return True


def test_analyse_button_skips_html5_validation():
    template = (ROOT / "templates" / "_paste_enquiry_panel.html").read_text()
    assert 'value="analyse_paste"' in template
    assert "formnovalidate" in template
    assert "Check these details" in template
    return True


def test_realistic_message_1():
    parsed = _parse(TEST_MESSAGES["TEST 1"])
    assert parsed["customer_name"] == "Steve"
    assert parsed["phone"] == "0412 345 678"
    assert parsed["pickup_address"] == "Cannington"
    assert parsed["delivery_address"] == "Innaloo"
    assert parsed["move_date"] == "2026-08-14"
    assert parsed["start_time"] == "09:00"
    assert "Time is approximate" in parsed["warnings"]
    assert "Full pickup street address not found" in parsed["warnings"]
    assert "Full delivery street address not found" in parsed["warnings"]
    return True


def test_realistic_message_2():
    parsed = _parse(TEST_MESSAGES["TEST 2"])
    assert parsed["customer_name"] == "Sarah Brown"
    assert parsed["phone"] == "0412 987 654"
    assert parsed["pickup_address"] == "15 Albany Hwy, Victoria Park"
    assert parsed["delivery_address"] == "28 Scarborough Beach Rd, North Perth"
    assert parsed["move_date"] == "2026-08-15"
    assert parsed["start_time"] == "08:00"
    assert "stairs" in parsed["notes"].lower()
    assert "piano" in parsed["notes"].lower()
    assert "Full pickup street address not found" not in parsed["warnings"]
    return True


def test_realistic_message_3():
    parsed = _parse(TEST_MESSAGES["TEST 3"])
    assert parsed["customer_name"] == "John"
    assert parsed["phone"] == "0413 555 666"
    assert parsed["pickup_address"] == "Morley"
    assert parsed["delivery_address"] == "Balcatta"
    assert parsed["move_date"] == "2026-08-10"
    assert parsed["start_time"] == ""
    assert "Time is vague" in " ".join(parsed["warnings"])
    assert "packing" in parsed["notes"].lower()
    return True


def test_realistic_message_4():
    parsed = _parse(TEST_MESSAGES["TEST 4"])
    assert parsed["customer_name"] == "Michael"
    assert parsed["phone"] == "0400 111 222"
    assert parsed["email"] == "test@example.com"
    assert parsed["pickup_address"] == "10 Smith Street, Cannington"
    assert parsed["delivery_address"] == "5 Jones Road, Como"
    assert parsed["move_date"] == "2026-08-10"
    assert "Full pickup street address not found" not in parsed["warnings"]
    return True


def test_realistic_message_5():
    parsed = _parse(TEST_MESSAGES["TEST 5"])
    assert parsed["customer_name"] == ""
    assert parsed["phone"] == "0412 111 222"
    assert parsed["pickup_address"] == "Cannington"
    assert parsed["delivery_address"] == "Innaloo"
    assert parsed["move_date"] == ""
    assert "Date could not be confidently determined" in parsed["warnings"]
    assert "Customer name not found" in parsed["warnings"]
    return True


def test_address_strips_trailing_schedule_from_delivery():
    text = (
        "We need to move from 25 Station St, Cannington to "
        "8 Scarborough Beach Rd, Innaloo next Saturday around 9am."
    )
    parsed = _parse(text)
    assert parsed["pickup_address"] == "25 Station St, Cannington"
    assert parsed["delivery_address"] == "8 Scarborough Beach Rd, Innaloo"
    assert parsed["move_date"] == "2026-08-15"
    assert parsed["start_time"] == "09:00"
    assert "Time is approximate" in parsed["warnings"]
    return True


def test_address_strips_trailing_schedule_suburb_move():
    parsed = _parse("from Cannington to Innaloo tomorrow at 8am")
    assert parsed["pickup_address"] == "Cannington"
    assert parsed["delivery_address"] == "Innaloo"
    assert parsed["move_date"] == "2026-08-10"
    assert parsed["start_time"] == "08:00"
    return True


def test_address_strips_trailing_schedule_standalone_to():
    parsed = _parse("to 5 Jones Rd, Como on Monday")
    assert parsed["delivery_address"] == "5 Jones Rd, Como"
    assert parsed["move_date"] == "2026-08-10"
    return True


def test_address_strips_trailing_schedule_labelled_delivery():
    parsed = _parse("delivery 28 Scarborough Beach Rd, North Perth at 9:30am")
    assert parsed["delivery_address"] == "28 Scarborough Beach Rd, North Perth"
    assert parsed["start_time"] == "09:30"
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
    assert notes and "packing" in notes.group(1).lower()
    assert "Extracted details" in html
    return True


def test_analyse_paste_shows_warnings_for_ambiguous_message():
    import auth
    import database as db
    from app import app

    db.init_db()
    uid = db.create_staff_user(
        "paste-warn-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Paste Warn",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "paste-warn"

    resp = client.post(
        "/bookings/new",
        data={"action": "analyse_paste", "paste_text": TEST_MESSAGES["TEST 5"]},
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Check these details" in html
    assert "Date could not be confidently determined" in html
    return True


def main():
    tests = [
        test_sample_message_extracts_core_fields,
        test_summary_rows_match_ui_labels,
        test_apply_parsed_fields_prefills_form,
        test_empty_paste_returns_blank_fields,
        test_analyse_button_skips_html5_validation,
        test_realistic_message_1,
        test_realistic_message_2,
        test_realistic_message_3,
        test_realistic_message_4,
        test_realistic_message_5,
        test_address_strips_trailing_schedule_from_delivery,
        test_address_strips_trailing_schedule_suburb_move,
        test_address_strips_trailing_schedule_standalone_to,
        test_address_strips_trailing_schedule_labelled_delivery,
        test_analyse_paste_prefills_new_booking_form,
        test_analyse_paste_shows_warnings_for_ambiguous_message,
        test_labelled_first_last_name_and_new_address_is,
        test_moving_to_dual_street_addresses,
        test_destination_is_dual_street_addresses,
        test_new_place_is_dual_street_addresses,
        test_structured_fields_not_in_notes,
        test_invalid_tokens_never_used_as_addresses,
        test_kerrie_matthews_pick_up_is_and_destination_is,
        test_us_on_here_is_never_used_as_customer_name,
        test_one_line_pickup_and_drop_off_labels,
        test_moving_from_to_with_phone_on_same_line,
        test_pick_up_from_deliver_to_around_9am,
        test_analyse_paste_prefills_kerrie_matthews_form,
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
