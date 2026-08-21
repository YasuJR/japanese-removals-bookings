#!/usr/bin/env python3
"""E2E tests — Dashboard TODAY & UPCOMING / UNPAID / PAID groups."""

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
    SECTION_PAID,
    SECTION_TODAY_UPCOMING,
    SECTION_UNPAID,
    UPCOMING_DIVIDER_LABEL,
    dashboard_section_group,
    dashboard_section_indexes,
    is_dashboard_paid,
    perth_today,
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


def test_section_index_helpers():
    today = "2026-08-21"
    assert dashboard_section_group({"payment_status": "Unpaid", "move_date": today}, today) == SECTION_TODAY_UPCOMING
    assert dashboard_section_group({"payment_status": "Part Paid", "move_date": today}, today) == SECTION_TODAY_UPCOMING
    assert dashboard_section_group({"payment_status": "Overdue", "move_date": "2026-08-20"}, today) == SECTION_UNPAID
    assert dashboard_section_group({"payment_status": "Paid", "move_date": today}, today) == SECTION_PAID
    assert dashboard_section_group({"payment_status": "Paid", "move_date": "2026-08-20"}, today) == SECTION_PAID
    assert is_dashboard_paid({"payment_status": "Paid"}) is True
    assert is_dashboard_paid({"payment_status": "Part Paid"}) is False

    # Display order: today unpaid, past unpaid, paid.
    jobs = [
        {"payment_status": "Unpaid", "move_date": "2026-08-21"},
        {"payment_status": "Part Paid", "move_date": "2026-08-20"},
        {"payment_status": "Paid", "move_date": "2026-08-17"},
        {"payment_status": "Paid", "move_date": "2026-08-22"},
    ]
    assert dashboard_section_indexes(jobs, today) == (0, 1, 2)
    assert dashboard_section_indexes([], today) == (None, None, None)
    assert dashboard_section_indexes([{"payment_status": "Paid", "move_date": today}], today) == (
        None,
        None,
        0,
    )
    assert dashboard_section_indexes(
        [{"payment_status": "Overdue", "move_date": "2026-08-20"}], today
    ) == (None, 0, None)
    return True


def test_today_upcoming_unpaid_then_paid_are_independent_groups():
    today = perth_today()
    yuki = _unique("Yuki Tamura")
    sam = _unique("Sam")
    lee = _unique("Lee")
    natalie = _unique("Natalie")
    kate = _unique("Kate")
    yuki_id = _create_job(yuki, today.isoformat(), "Confirmed", "Unpaid")
    sam_id = _create_job(sam, today.isoformat(), "Confirmed", "Unpaid")
    _create_job(lee, (today - timedelta(days=2)).isoformat(), "Confirmed", "Unpaid")
    _create_job(natalie, (today - timedelta(days=1)).isoformat(), "Confirmed", "Unpaid")
    kate_id = _create_job(kate, (today - timedelta(days=3)).isoformat(), "Confirmed", "Paid")
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)

    today_header = html.find("dashboard-upcoming-divider-row")
    unpaid_header = html.find("dashboard-unpaid-divider-row")
    paid_header = html.find("dashboard-paid-divider-row")
    yuki_pos = html.find(yuki)
    sam_pos = html.find(sam)
    lee_pos = html.find(lee)
    natalie_pos = html.find(natalie)
    kate_pos = html.find(kate)
    assert today_header != -1 and unpaid_header != -1 and paid_header != -1
    assert today_header < yuki_pos < unpaid_header
    assert today_header < sam_pos < unpaid_header
    assert unpaid_header < lee_pos < paid_header
    assert unpaid_header < natalie_pos < paid_header
    assert paid_header < kate_pos
    assert today_header < unpaid_header < paid_header
    assert UPCOMING_DIVIDER_LABEL in html or "TODAY &amp; UPCOMING" in html
    assert PAYMENT_UNPAID_DIVIDER_LABEL in html
    assert PAYMENT_PAID_DIVIDER_LABEL in html

    wanted = [yuki, sam, lee, natalie, kate]
    names = [name for name in _customer_names(html) if name in wanted]
    assert names[:2] == [yuki, sam], names
    assert set(names[2:4]) == {lee, natalie}, names
    assert names[-1] == kate, names
    assert names.count(yuki) == 1 and names.count(sam) == 1
    assert names.count(lee) == 1 and names.count(kate) == 1

    assert dict(db.get_booking(yuki_id))["status"] == "Confirmed"
    assert dict(db.get_booking(sam_id))["status"] == "Confirmed"
    assert dict(db.get_booking(yuki_id))["payment_status"] == "Unpaid"
    assert dict(db.get_booking(kate_id))["payment_status"] == "Paid"
    return True


def test_paid_jobs_stay_in_paid_group_regardless_of_date():
    today = perth_today()
    paid_today = _unique("Paid Today Customer")
    paid_future = _unique("Paid Future Customer")
    unpaid_today = _unique("Unpaid Today Customer")
    _create_job(paid_today, today.isoformat(), "Confirmed", "Paid")
    _create_job(paid_future, (today + timedelta(days=2)).isoformat(), "Confirmed", "Paid")
    _create_job(unpaid_today, today.isoformat(), "Confirmed", "Unpaid")
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    today_header = html.find("dashboard-upcoming-divider-row")
    unpaid_header = html.find("dashboard-unpaid-divider-row")
    paid_header = html.find("dashboard-paid-divider-row")
    unpaid_today_pos = html.find(unpaid_today)
    paid_today_pos = html.find(paid_today)
    paid_future_pos = html.find(paid_future)
    assert today_header < unpaid_today_pos
    if unpaid_header != -1:
        assert unpaid_today_pos < unpaid_header
    assert paid_header < paid_today_pos
    assert paid_header < paid_future_pos
    assert unpaid_today_pos < paid_header
    return True


def test_part_paid_and_overdue_past_jobs_are_in_unpaid_group():
    today = perth_today()
    part_name = _unique("Part Paid Customer")
    overdue_name = _unique("Overdue Customer")
    paid_name = _unique("Fully Paid Customer")
    _create_job(
        part_name,
        (today - timedelta(days=1)).isoformat(),
        "Confirmed",
        invoice.PAYMENT_STATUS_PART_PAID,
    )
    _create_job(
        overdue_name,
        (today - timedelta(days=2)).isoformat(),
        "Confirmed",
        invoice.PAYMENT_STATUS_OVERDUE,
    )
    _create_job(paid_name, today.isoformat(), "Confirmed", invoice.PAYMENT_STATUS_PAID)
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)

    unpaid_header = html.find("dashboard-unpaid-divider-row")
    paid_header = html.find("dashboard-paid-divider-row")
    part_pos = html.find(part_name)
    overdue_pos = html.find(overdue_name)
    paid_pos = html.find(paid_name)
    assert unpaid_header != -1 and paid_header != -1
    assert unpaid_header < part_pos < paid_header
    assert unpaid_header < overdue_pos < paid_header
    assert paid_header < paid_pos
    return True


def test_status_order_is_preserved_within_each_group():
    today = perth_today()
    paid_confirmed = _unique("Paid Confirmed First Date")
    unpaid_invoiced = _unique("Unpaid Invoiced")
    unpaid_completed = _unique("Unpaid Completed")
    unpaid_confirmed = _unique("Unpaid Confirmed")
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
        test_section_index_helpers,
        test_today_upcoming_unpaid_then_paid_are_independent_groups,
        test_paid_jobs_stay_in_paid_group_regardless_of_date,
        test_part_paid_and_overdue_past_jobs_are_in_unpaid_group,
        test_status_order_is_preserved_within_each_group,
        test_job_status_paid_is_not_payment_paid,
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
