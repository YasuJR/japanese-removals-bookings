#!/usr/bin/env python3
"""Search bookings by invoice number (case/space insensitive)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SECRET_KEY", "test-search-invoice")

import auth
import database as db
from app import app


def _ids(rows):
    return sorted(int(row["id"]) for row in rows)


def _create_with_invoice(stored_number: str, customer: str = "Andrew Carroll") -> int:
    booking_id = db.create_booking(
        customer,
        "0412345678",
        "andrew@example.com",
        "1 Pickup St, Perth WA",
        "2 Delivery Ave, Fremantle WA",
        "2099-06-01",
        2,
        "Invoice search marker",
        status="Invoiced",
    )
    db.update_booking_invoice_fields(
        booking_id, {"invoice_number": stored_number}
    )
    return booking_id


def test_inv56_variants_match_stored_56():
    db.init_db()
    booking_id = _create_with_invoice("56")
    queries = ["INV56", "inv56", "Inv56", "INV 56", "inv 56", "56"]
    for q in queries:
        rows = db.search_bookings(q)
        assert booking_id in _ids(rows), "missing for {0!r}".format(q)
    assert _ids(db.search_bookings("INV9999")) == []
    return True


def test_search_page_shows_invoice_link():
    db.init_db()
    booking_id = _create_with_invoice("56", customer="Search Page Inv")
    uid = db.create_staff_user(
        "inv-search-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Inv Search",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    html = client.get("/bookings/search?q=inv%2056").get_data(as_text=True)
    assert "Search Page Inv" in html
    assert "/bookings/{0}/invoice/preview".format(booking_id) in html
    assert "INV56" in html or "invoice/preview" in html
    return True


def test_existing_name_search_still_works():
    db.init_db()
    booking_id = db.create_booking(
        "Unique Name Zeta",
        "0400111222",
        "zeta@example.com",
        "10 A St",
        "20 B St",
        "2099-07-01",
        2,
        "",
    )
    assert booking_id in _ids(db.search_bookings("Unique Name Zeta"))
    assert booking_id in _ids(db.search_bookings("unique name zeta"))
    return True


def main() -> int:
    tests = [
        test_inv56_variants_match_stored_56,
        test_search_page_shows_invoice_link,
        test_existing_name_search_still_works,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS:", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", fn.__name__, exc)
    print("{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
