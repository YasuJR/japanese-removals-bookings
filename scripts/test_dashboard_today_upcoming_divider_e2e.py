#!/usr/bin/env python3
"""E2E tests — Dashboard TODAY & UPCOMING visual divider."""

import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
from app import app
from dashboard_data import (
    perth_today,
    upcoming_divider_index,
    upcoming_divider_indexes,
)


_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "dash-div-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Dash Divider Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_job(name, move_date, status="Confirmed"):
    return db.create_booking(
        name,
        "0412555666",
        "divider-{0}@example.com".format(os.getpid()),
        "1 Divider St, Perth WA",
        "2 Divider Ave, Fremantle WA",
        move_date,
        2,
        "dashboard divider test",
        status=status,
    )


def _unique(label):
    global _test_user_counter
    _test_user_counter += 1
    return "{0} {1}-{2}".format(label, os.getpid(), _test_user_counter)


def test_perth_today_uses_australia_perth():
    utc = ZoneInfo("UTC")
    # Perth is UTC+8 year-round. 16:00 UTC on 18 Aug is 00:00 on 19 Aug in Perth.
    assert perth_today(datetime(2026, 8, 18, 16, 0, tzinfo=utc)) == date(2026, 8, 19)
    assert perth_today(datetime(2026, 8, 18, 15, 59, tzinfo=utc)) == date(2026, 8, 18)
    assert perth_today(datetime(2026, 8, 19, 0, 0, tzinfo=ZoneInfo("Australia/Perth"))) == date(
        2026, 8, 19
    )
    return True


def test_upcoming_divider_index_helpers():
    today = "2026-08-19"
    jobs = [
        {"move_date": "2026-08-18", "id": 1},
        {"move_date": "2026-08-19", "id": 2},
        {"move_date": "2026-08-20", "id": 3},
    ]
    assert upcoming_divider_index(jobs, today) == 1
    assert upcoming_divider_index(jobs, "2026-08-21") is None
    assert upcoming_divider_index(jobs, "2026-08-17") == 0
    assert upcoming_divider_index([], today) is None
    grouped = [
        {"payment_status": "Unpaid", "move_date": "2026-08-18"},
        {"payment_status": "Unpaid", "move_date": "2026-08-19"},
        {"payment_status": "Paid", "move_date": "2026-08-17"},
        {"payment_status": "Paid", "move_date": "2026-08-20"},
    ]
    assert upcoming_divider_indexes(grouped, today) == [1, 3]
    # Postgres-style date objects must compare the same way.
    assert (
        upcoming_divider_index(
            [{"move_date": date(2026, 8, 18)}, {"move_date": date(2026, 8, 19)}],
            today,
        )
        == 1
    )
    return True


def test_dashboard_divider_between_past_and_upcoming():
    today = perth_today()
    past = (today - timedelta(days=1)).isoformat()
    upcoming = today.isoformat()
    past_name = _unique("Past Divider Customer")
    upcoming_name = _unique("Upcoming Divider Customer")
    past_id = _create_job(past_name, past, "Confirmed")
    upcoming_id = _create_job(upcoming_name, upcoming, "Confirmed")
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)

    assert "TODAY &amp; UPCOMING" in html or "TODAY & UPCOMING" in html
    assert "dashboard-upcoming-divider-row" in html
    assert 'colspan="10"' in html
    assert 'aria-label="Today and upcoming"' in html

    past_pos = html.find(past_name)
    upcoming_pos = html.find(upcoming_name)
    assert past_pos != -1 and upcoming_pos != -1
    assert past_pos < upcoming_pos
    assert "dashboard-upcoming-divider-row" in html[past_pos:upcoming_pos]

    assert dict(db.get_booking(past_id))["status"] == "Confirmed"
    assert dict(db.get_booking(upcoming_id))["status"] == "Confirmed"
    from dashboard_data import job_move_date_iso

    assert job_move_date_iso(db.get_booking(past_id)) == past
    assert job_move_date_iso(db.get_booking(upcoming_id)) == upcoming
    return True


def test_no_divider_when_all_jobs_are_in_the_past():
    today = perth_today()
    jobs = [
        {"move_date": (today - timedelta(days=2)).isoformat()},
        {"move_date": (today - timedelta(days=1)).isoformat()},
    ]
    assert upcoming_divider_index(jobs, today.isoformat()) is None
    return True


def test_divider_at_top_when_all_visible_jobs_are_upcoming():
    today = perth_today()
    future = (today + timedelta(days=1)).isoformat()
    future_name = _unique("Only Future Customer")
    _create_job(future_name, future, "Confirmed")
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    assert future_name in html
    divider_pos = html.find("dashboard-upcoming-divider-row")
    job_pos = html.find(future_name)
    assert divider_pos != -1 and job_pos != -1
    assert divider_pos < job_pos
    return True


def test_divider_does_not_change_job_order_or_status():
    today = perth_today()
    past = (today - timedelta(days=1)).isoformat()
    upcoming = (today + timedelta(days=2)).isoformat()
    bravo = _unique("Order Bravo Confirmed")
    alpha = _unique("Order Alpha Confirmed")
    second = _create_job(bravo, past, "Confirmed")
    first = _create_job(alpha, upcoming, "Confirmed")
    client = _login_client()
    html = client.get("/dashboard?filter=all&jobs_limit=500").get_data(as_text=True)
    names = re.findall(
        r'class="customer-link"[^>]*>\s*<strong>([^<]+)</strong>',
        html,
    )
    confirmed = [name for name in names if name in (bravo, alpha)]
    assert confirmed == [bravo, alpha], confirmed
    assert dict(db.get_booking(first))["status"] == "Confirmed"
    assert dict(db.get_booking(second))["status"] == "Confirmed"
    return True


def test_mobile_css_covers_divider():
    css = (ROOT / "static" / "mobile.css").read_text()
    assert "@media (max-width: 767px)" in css
    assert ".dashboard-upcoming-divider-label" in css
    assert ".dashboard-section-divider-label" in css
    desktop = (ROOT / "static" / "style.css").read_text()
    assert "height: 4px" in desktop
    assert "#083d28" in desktop
    assert ".dashboard-section-divider-label" in desktop
    return True


def main():
    tests = [
        test_perth_today_uses_australia_perth,
        test_upcoming_divider_index_helpers,
        test_dashboard_divider_between_past_and_upcoming,
        test_no_divider_when_all_jobs_are_in_the_past,
        test_divider_at_top_when_all_visible_jobs_are_upcoming,
        test_divider_does_not_change_job_order_or_status,
        test_mobile_css_covers_divider,
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
