#!/usr/bin/env python3
"""E2E tests — Dashboard UNPAID / PAID section dividers."""

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
import invoice
from app import app
from dashboard_data import (
    PAYMENT_PAID_DIVIDER_LABEL,
    PAYMENT_UNPAID_DIVIDER_LABEL,
    is_dashboard_paid,
    payment_section_indexes,
    perth_today,
    upcoming_divider_indexes,
)


_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "dash-pay-div-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Pay Divider Test")
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
        "0412555777",
        "pay-div-{0}@example.com".format(os.getpid()),
        "1 Pay Divider St, Perth WA",
        "2 Pay Divider Ave, Fremantle WA",
        move_date,
        2,
        "dashboard payment divider test",
        status=status,
        payment_status=payment_status,
    )


def _customer_names(html):
    return re.findall(
        r'class="customer-link"[^>]*>\s*<strong>([^<]+)</strong>',
        html,
    )


def test_payment_section_index_helpers():
    jobs = [
        {"payment_status": "Unpaid", "move_date": "2026-08-18"},
        {"payment_status": "Part Paid", "move_date": "2026-08-19"},
        {"payment_status": "Paid", "move_date": "2026-08-17"},
        {"payment_status": "Paid", "move_date": "2026-08-20"},
    ]
    assert payment_section_indexes(jobs) == (0, 2)
    assert payment_section_indexes([]) == (None, None)
    assert payment_section_indexes([{"payment_status": "Paid"}]) == (None, 0)
    assert payment_section_indexes([{"payment_status": "Overdue"}]) == (0, None)
    assert is_dashboard_paid({"payment_status": "Paid"}) is True
    assert is_dashboard_paid({"payment_status": "Part Paid"}) is False
    assert is_dashboard_paid({"payment_status": "Overdue"}) is False
    assert is_dashboard_paid({"payment_status": "Unpaid"}) is False
    assert upcoming_divider_indexes(jobs, "2026-08-19") == [1, 3]
    return True


def test_dashboard_groups_unpaid_then_paid_with_headers():
    today = perth_today()
    unpaid_name = _unique("Unpaid Group Customer")
    paid_name = _unique("Paid Group Customer")
    unpaid_id = _create_job(unpaid_name, today.isoformat(), "Confirmed", "Unpaid")
    paid_id = _create_job(paid_name, today.isoformat(), "Confirmed", "Paid")
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)

    unpaid_header = html.find('dashboard-unpaid-divider-row')
    paid_header = html.find('dashboard-paid-divider-row')
    unpaid_pos = html.find(unpaid_name)
    paid_pos = html.find(paid_name)
    assert unpaid_header != -1 and paid_header != -1
    assert unpaid_pos != -1 and paid_pos != -1
    assert unpaid_header < unpaid_pos < paid_header < paid_pos
    assert PAYMENT_UNPAID_DIVIDER_LABEL in html
    assert PAYMENT_PAID_DIVIDER_LABEL in html
    assert 'aria-label="Unpaid"' in html
    assert 'aria-label="Paid"' in html

    names = [name for name in _customer_names(html) if name in (unpaid_name, paid_name)]
    assert names == [unpaid_name, paid_name], names

    assert dict(db.get_booking(unpaid_id))["status"] == "Confirmed"
    assert dict(db.get_booking(paid_id))["status"] == "Confirmed"
    assert dict(db.get_booking(unpaid_id))["payment_status"] == "Unpaid"
    assert dict(db.get_booking(paid_id))["payment_status"] == "Paid"
    return True


def test_part_paid_and_overdue_stay_in_unpaid_group():
    today = perth_today()
    part_name = _unique("Part Paid Customer")
    overdue_name = _unique("Overdue Customer")
    paid_name = _unique("Fully Paid Customer")
    _create_job(part_name, today.isoformat(), "Confirmed", invoice.PAYMENT_STATUS_PART_PAID)
    _create_job(overdue_name, today.isoformat(), "Confirmed", invoice.PAYMENT_STATUS_OVERDUE)
    _create_job(paid_name, today.isoformat(), "Confirmed", invoice.PAYMENT_STATUS_PAID)
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)

    paid_header = html.find("dashboard-paid-divider-row")
    part_pos = html.find(part_name)
    overdue_pos = html.find(overdue_name)
    paid_pos = html.find(paid_name)
    assert paid_header != -1
    assert part_pos != -1 and overdue_pos != -1 and paid_pos != -1
    assert part_pos < paid_header
    assert overdue_pos < paid_header
    assert paid_header < paid_pos
    return True


def test_status_order_is_preserved_within_payment_group():
    today = perth_today()
    paid_confirmed = _unique("Paid Confirmed First Date")
    unpaid_invoiced = _unique("Unpaid Invoiced")
    unpaid_completed = _unique("Unpaid Completed")
    unpaid_confirmed = _unique("Unpaid Confirmed")
    # Paid Confirmed is earlier by date; it must still come after all UNPAID rows.
    _create_job(
        paid_confirmed,
        (today - timedelta(days=3)).isoformat(),
        "Confirmed",
        "Paid",
    )
    _create_job(
        unpaid_invoiced,
        (today - timedelta(days=2)).isoformat(),
        "Invoiced",
        "Unpaid",
    )
    _create_job(
        unpaid_completed,
        (today - timedelta(days=1)).isoformat(),
        "Completed",
        "Unpaid",
    )
    unpaid_confirmed_id = _create_job(
        unpaid_confirmed,
        today.isoformat(),
        "Confirmed",
        "Unpaid",
    )
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    wanted = [unpaid_confirmed, unpaid_completed, unpaid_invoiced, paid_confirmed]
    names = [name for name in _customer_names(html) if name in wanted]
    assert names == wanted, names
    assert dict(db.get_booking(unpaid_confirmed_id))["status"] == "Confirmed"
    return True


def test_job_status_paid_is_not_payment_paid():
    today = perth_today()
    job_paid_unpaid = _unique("Job Status Paid Unpaid Payment")
    payment_paid = _unique("Payment Paid Confirmed")
    _create_job(job_paid_unpaid, today.isoformat(), "Paid", "Unpaid")
    _create_job(payment_paid, today.isoformat(), "Confirmed", "Paid")
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    paid_header = html.find("dashboard-paid-divider-row")
    job_paid_pos = html.find(job_paid_unpaid)
    payment_paid_pos = html.find(payment_paid)
    assert paid_header != -1
    assert job_paid_pos < paid_header < payment_paid_pos
    return True


def test_today_upcoming_still_splits_past_and_future_inside_unpaid():
    today = perth_today()
    past_name = _unique("Unpaid Past Divider")
    upcoming_name = _unique("Unpaid Upcoming Divider")
    paid_past = _unique("Paid Past After Unpaid")
    _create_job(past_name, (today - timedelta(days=1)).isoformat(), "Confirmed", "Unpaid")
    _create_job(upcoming_name, today.isoformat(), "Confirmed", "Unpaid")
    _create_job(paid_past, (today - timedelta(days=2)).isoformat(), "Confirmed", "Paid")
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)

    past_pos = html.find(past_name)
    upcoming_pos = html.find(upcoming_name)
    paid_pos = html.find(paid_past)
    paid_header = html.find("dashboard-paid-divider-row")
    assert past_pos != -1 and upcoming_pos != -1 and paid_pos != -1
    slice_html = html[past_pos:upcoming_pos]
    assert "dashboard-upcoming-divider-row" in slice_html
    assert "TODAY" in slice_html
    assert past_pos < upcoming_pos < paid_header < paid_pos
    return True


def test_mobile_and_desktop_css_cover_payment_dividers():
    css = (ROOT / "static" / "mobile.css").read_text()
    assert ".dashboard-section-divider-label" in css
    desktop = (ROOT / "static" / "style.css").read_text()
    assert ".dashboard-section-divider-label" in desktop
    assert "height: 4px" in desktop
    assert "#083d28" in desktop
    return True


def main():
    tests = [
        test_payment_section_index_helpers,
        test_dashboard_groups_unpaid_then_paid_with_headers,
        test_part_paid_and_overdue_stay_in_unpaid_group,
        test_status_order_is_preserved_within_payment_group,
        test_job_status_paid_is_not_payment_paid,
        test_today_upcoming_still_splits_past_and_future_inside_unpaid,
        test_mobile_and_desktop_css_cover_payment_dividers,
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
