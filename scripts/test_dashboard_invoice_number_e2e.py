#!/usr/bin/env python3
"""E2E tests — Dashboard shows issued invoice numbers under customer names."""

import os
import re
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import invoice_numbering
from app import app
from dashboard_data import perth_today


_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "dash-inv-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Dash Invoice Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _unique(label):
    global _test_user_counter
    _test_user_counter += 1
    return "{0} {1}-{2}".format(label, os.getpid(), _test_user_counter)


def _create_job(name, move_date, status="Confirmed", payment_status="Unpaid"):
    return db.create_booking(
        name,
        "0412555888",
        "dash-inv-{0}@example.com".format(os.getpid()),
        "1 Invoice St, Perth WA",
        "2 Invoice Ave, Fremantle WA",
        move_date,
        2,
        "dashboard invoice number test",
        status=status,
        payment_status=payment_status,
    )


def _customer_block(html, name):
    idx = html.find("<strong>{0}</strong>".format(name))
    if idx == -1:
        return ""
    return html[idx : idx + 420]


def test_stored_invoice_number_display_helper():
    assert invoice_numbering.stored_invoice_number_display({}) == ""
    assert invoice_numbering.stored_invoice_number_display({"id": 99}) == ""
    assert invoice_numbering.stored_invoice_number_display(
        {"id": 99, "invoice_number": ""}
    ) == ""
    assert invoice_numbering.stored_invoice_number_display(
        {"invoice_number": "25"}
    ) == "INV25"
    assert invoice_numbering.stored_invoice_number_display(
        {"invoice_number": "INV-118"}
    ) == "INV118"
    assert invoice_numbering.stored_invoice_number_display(
        {"invoice_number": "INV125"}
    ) == "INV125"
    return True


def test_dashboard_shows_issued_invoice_under_customer_name():
    today = perth_today()
    named = _unique("Kate")
    unnamed = _unique("No Invoice Customer")
    named_id = _create_job(named, today.isoformat(), "Confirmed", "Unpaid")
    unnamed_id = _create_job(unnamed, today.isoformat(), "Confirmed", "Unpaid")
    db.update_booking_invoice_fields(named_id, {"invoice_number": "123"})
    before_unnamed = dict(db.get_booking(unnamed_id))["invoice_number"]
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)

    named_block = _customer_block(html, named)
    unnamed_block = _customer_block(html, unnamed)
    assert named in html and unnamed in html
    assert 'href="/bookings/{0}"'.format(named_id) in html
    assert 'class="customer-link"' in named_block or 'customer-link' in html
    assert 'class="dashboard-customer-invoice"' in named_block
    assert "INV123" in named_block
    assert "dashboard-customer-invoice" not in unnamed_block
    assert dict(db.get_booking(named_id))["invoice_number"] == "123"
    assert dict(db.get_booking(unnamed_id))["invoice_number"] == before_unnamed
    return True


def test_invoice_number_appears_in_all_dashboard_groups():
    today = perth_today()
    upcoming = _unique("Sam")
    unpaid = _unique("John")
    paid = _unique("Kate Paid")
    upcoming_id = _create_job(upcoming, today.isoformat(), "Confirmed", "Unpaid")
    unpaid_id = _create_job(
        unpaid, (today - timedelta(days=2)).isoformat(), "Confirmed", "Unpaid"
    )
    paid_id = _create_job(paid, today.isoformat(), "Confirmed", "Paid")
    db.update_booking_invoice_fields(upcoming_id, {"invoice_number": "INV-125"})
    db.update_booking_invoice_fields(unpaid_id, {"invoice_number": "125"})
    db.update_booking_invoice_fields(paid_id, {"invoice_number": "INV118"})
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)

    assert "dashboard-upcoming-divider-row" in html
    assert "dashboard-unpaid-divider-row" in html
    assert "dashboard-paid-divider-row" in html
    assert "INV125" in _customer_block(html, upcoming)
    assert "INV125" in _customer_block(html, unpaid)
    assert "INV118" in _customer_block(html, paid)
    upcoming_pos = html.find(upcoming)
    unpaid_pos = html.find(unpaid)
    paid_pos = html.find(paid)
    assert upcoming_pos < unpaid_pos < paid_pos
    return True


def test_does_not_allocate_invoice_numbers_on_dashboard_view():
    today = perth_today()
    name = _unique("Unissued")
    booking_id = _create_job(name, today.isoformat())
    before = dict(db.get_booking(booking_id))
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    after = dict(db.get_booking(booking_id))
    assert name in html
    assert not (before.get("invoice_number") or "").strip()
    assert after.get("invoice_number") == before.get("invoice_number")
    fake = "INV{0}".format(booking_id)
    assert fake not in _customer_block(html, name)
    return True


def test_customer_name_stays_clickable():
    today = perth_today()
    name = _unique("Clickable")
    booking_id = _create_job(name, today.isoformat())
    db.update_booking_invoice_fields(booking_id, {"invoice_number": "31"})
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    match = re.search(
        r'<a class="customer-link" href="[^"]*\/bookings\/{0}"><strong>{1}</strong></a>'.format(
            booking_id, re.escape(name)
        ),
        html,
    )
    assert match, _customer_block(html, name)
    assert "INV31" in _customer_block(html, name)
    return True


def test_css_keeps_invoice_number_smaller_and_muted():
    desktop = (ROOT / "static" / "style.css").read_text()
    mobile = (ROOT / "static" / "mobile.css").read_text()
    assert ".dashboard-customer-invoice" in desktop
    assert "font-size: 0.72rem" in desktop
    assert "var(--muted)" in desktop.split(".dashboard-customer-invoice")[1][:280]
    assert ".dashboard-customer-invoice" in mobile
    return True


def main():
    tests = [
        test_stored_invoice_number_display_helper,
        test_dashboard_shows_issued_invoice_under_customer_name,
        test_invoice_number_appears_in_all_dashboard_groups,
        test_does_not_allocate_invoice_numbers_on_dashboard_view,
        test_customer_name_stays_clickable,
        test_css_keeps_invoice_number_smaller_and_muted,
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
