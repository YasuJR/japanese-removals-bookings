#!/usr/bin/env python3
"""E2E tests — Search bookings is case-insensitive across all search fields."""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
from app import app

_test_user_counter = 0
_test_booking_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    uid = db.create_staff_user(
        "search-case-{0}-{1}".format(os.getpid(), _test_user_counter),
        auth.hash_password("test"),
        "Search Case Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _ids(rows):
    return [int(row["id"]) for row in rows]


def _create_kate():
    global _test_booking_counter
    _test_booking_counter += 1
    marker = "KateCase{0}{1}{2}".format(os.getpid(), _test_booking_counter, time.time_ns())
    booking_id = db.create_booking(
        "Kate",
        "0412{0:06d}".format(os.getpid() % 1000000),
        "{0}@example.com".format(marker.lower()),
        "{0} Pickup Road, Cloverdale".format(marker),
        "{0} Delivery Street, Piara Waters".format(marker),
        "2099-11-02",
        2,
        "Notes for {0}".format(marker),
        start_time="08:00",
        finish_time="10:00",
        duration_hours="2",
        status="Confirmed",
    )
    return booking_id, marker


def test_kate_name_all_casings_return_same_booking():
    booking_id, _marker = _create_kate()
    queries = ["Kate", "kate", "KATE", "KaTe"]
    result_sets = []
    for query in queries:
        rows = db.search_bookings(query)
        ids = _ids(rows)
        assert booking_id in ids, "{0} missing Kate booking {1}".format(query, booking_id)
        kate_rows = [
            row for row in rows if int(row["id"]) == booking_id
        ]
        assert len(kate_rows) == 1, query
        assert kate_rows[0]["customer_name"] == "Kate"
        result_sets.append(booking_id)
    assert len(set(result_sets)) == 1
    return True


def test_kate_search_page_all_casings():
    booking_id, marker = _create_kate()
    client = _login_client()
    found = []
    for query in ("Kate", "kate", "KATE", "KaTe"):
        html = client.get("/bookings/search?q={0}".format(query)).get_data(as_text=True)
        assert "Staff login" not in html, query
        assert "booking-card-name" in html, query
        assert ">Kate<" in html or "Kate</h3>" in html or ">Kate</" in html
        assert "/bookings/{0}/edit".format(booking_id) in html, query
        found.append(booking_id)
    assert found == [booking_id] * 4

    for query in (marker, marker.lower(), marker.upper()):
        rows = db.search_bookings(query)
        ids = _ids(rows)
        assert ids == [booking_id], (query, ids)
        html = client.get("/bookings/search?q={0}".format(query)).get_data(as_text=True)
        assert "1 result" in html, query
        assert "/bookings/{0}/edit".format(booking_id) in html
    return True


def test_all_search_fields_are_case_insensitive():
    marker = "FieldCase{0}".format(os.getpid())
    booking_id = db.create_booking(
        "Stored Mixed {0}".format(marker),
        "0400FIELD",
        "Mixed.Email.{0}@Example.COM".format(marker),
        "12 Mixed Pickup Avenue",
        "88 Mixed Delivery Boulevard",
        "2099-11-03",
        2,
        "Unique Notes Token {0}".format(marker),
        start_time="09:00",
        finish_time="11:00",
        duration_hours="2",
        status="Confirmed",
    )
    samples = {
        "customer": ("stored mixed {0}".format(marker.lower()), "STORED MIXED {0}".format(marker.upper())),
        "phone": ("0400field", "0400FIELD"),
        "email": (
            "mixed.email.{0}@example.com".format(marker.lower()),
            "MIXED.EMAIL.{0}@EXAMPLE.COM".format(marker.upper()),
        ),
        "pickup": ("12 mixed pickup avenue", "12 MIXED PICKUP AVENUE"),
        "delivery": ("88 mixed delivery boulevard", "88 MIXED DELIVERY BOULEVARD"),
        "notes": (
            "unique notes token {0}".format(marker.lower()),
            "UNIQUE NOTES TOKEN {0}".format(marker.upper()),
        ),
        "booking_number": (str(booking_id), str(booking_id)),
    }
    for field, (lower_q, upper_q) in samples.items():
        lower_ids = _ids(db.search_bookings(lower_q))
        upper_ids = _ids(db.search_bookings(upper_q))
        assert booking_id in lower_ids, field
        assert booking_id in upper_ids, field
        if field != "booking_number":
            assert lower_ids == upper_ids or (
                booking_id in lower_ids and booking_id in upper_ids
            )
    row = dict(db.get_booking(booking_id))
    assert row["customer_name"] == "Stored Mixed {0}".format(marker)
    assert row["email"] == "Mixed.Email.{0}@Example.COM".format(marker)
    return True


def test_search_does_not_alter_stored_name():
    booking_id, _marker = _create_kate()
    before = dict(db.get_booking(booking_id))
    db.search_bookings("kate")
    db.search_bookings("KATE")
    after = dict(db.get_booking(booking_id))
    assert after["customer_name"] == "Kate"
    assert after["customer_name"] == before["customer_name"]
    assert after["phone"] == before["phone"]
    assert after["email"] == before["email"]
    assert after["notes"] == before["notes"]
    return True


def main():
    tests = [
        test_kate_name_all_casings_return_same_booking,
        test_kate_search_page_all_casings,
        test_all_search_fields_are_case_insensitive,
        test_search_does_not_alter_stored_name,
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
