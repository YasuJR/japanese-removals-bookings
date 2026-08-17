#!/usr/bin/env python3
"""E2E tests — Daily Jobs calendar page."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import daily_jobs_data
import database as db
from app import app


_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    uid = db.create_staff_user(
        "daily-jobs-{0}-{1}".format(os.getpid(), _test_user_counter),
        auth.hash_password("test"),
        "Daily Jobs Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _create_booking(customer, move_date, start_time, finish_time, crew="Yasu"):
    return db.create_booking(
        customer,
        "0412000123",
        "daily-jobs@example.com",
        "1 Pickup St, Perth WA",
        "2 Delivery Ave, Fremantle WA",
        move_date,
        3,
        "Daily jobs test",
        start_time=start_time,
        finish_time=finish_time,
        duration_hours="4",
        status="Confirmed",
        crew=crew,
    )


def test_daily_jobs_sorted_by_start_time():
    move_date = "2099-01-{0:02d}".format(os.getpid() % 28 + 1)
    _create_booking("Late Customer", move_date, "13:00", "18:00")
    _create_booking("Early Customer", move_date, "08:00", "12:00")

    daily = daily_jobs_data.build_daily_jobs(move_date)
    assert daily["summary"]["total_jobs"] == 2
    assert daily["jobs"][0]["customer_name"] == "Early Customer"
    assert daily["jobs"][0]["job_label"] == "JOB 1"
    assert daily["jobs"][1]["customer_name"] == "Late Customer"
    assert daily["jobs"][1]["job_label"] == "JOB 2"
    return True


def test_daily_jobs_summary_times_and_crew():
    move_date = "2026-12-19"
    _create_booking("Job A", move_date, "08:00", "12:00", crew="Katsu,Will")
    _create_booking("Job B", move_date, "13:00", "18:00", crew="Yasu,Will")

    daily = daily_jobs_data.build_daily_jobs(move_date)
    assert daily["summary"]["earliest_start"] == "8:00 AM"
    assert daily["summary"]["latest_finish"] == "6:00 PM"
    assert "Katsu" in daily["summary"]["crew_display"]
    assert "Will" in daily["summary"]["crew_display"]
    assert "Yasu" in daily["summary"]["crew_display"]
    return True


def test_daily_jobs_page_renders_cards_and_links():
    move_date = "2026-12-20"
    booking_id = _create_booking("Max Meredith", move_date, "08:00", "12:00", crew="Katsu,Will,Yasu")
    client = _login_client()
    html = client.get("/calendar/daily/{0}".format(move_date)).get_data(as_text=True)

    assert "Daily Jobs" in html
    assert "JOB 1" in html
    assert "Max Meredith" in html
    assert "8:00 AM – 12:00 PM" in html
    assert "Crew:" in html
    assert "Katsu / Will / Yasu" in html
    assert "Pickup Map" in html
    assert "Delivery Map" in html
    assert 'href="/bookings/{0}"'.format(booking_id) in html
    assert "← Back to Calendar" in html
    assert "Total Jobs" in html
    return True


def test_calendar_page_includes_daily_jobs_navigation():
    client = _login_client()
    html = client.get("/calendar?view=month&year=2026&month=12").get_data(as_text=True)
    assert 'class="calendar-day"' in html
    assert "calendar.js" in html
    assert "calendar-day-panel" not in html
    return True


def main():
    tests = [
        test_daily_jobs_sorted_by_start_time,
        test_daily_jobs_summary_times_and_crew,
        test_daily_jobs_page_renders_cards_and_links,
        test_calendar_page_includes_daily_jobs_navigation,
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
