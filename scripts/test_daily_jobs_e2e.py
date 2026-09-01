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


def _create_booking(
    customer,
    move_date,
    start_time,
    finish_time,
    crew="Yasu",
    hourly_rate=0.0,
    callout_fee=0.0,
):
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
        hourly_rate=hourly_rate,
        callout_fee=callout_fee,
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
    assert "4hr" in html
    assert "Crew:" in html
    assert "Katsu / Will / Yasu" in html
    assert "Pickup Map" in html
    assert "Delivery Map" in html
    assert 'href="/bookings/{0}"'.format(booking_id) in html
    assert "← Back to Calendar" in html
    assert "Total Jobs" in html
    assert html.find("daily_jobs.css") < html.find("<main")
    assert "daily_jobs.css?v=" in html
    return True


def test_daily_jobs_duration_from_start_finish_times():
    assert daily_jobs_data.format_job_duration_label(2) == "2hr"
    assert daily_jobs_data.format_job_duration_label(2.0) == "2hr"
    assert daily_jobs_data.format_job_duration_label(2.5) == "2.5hr"
    assert daily_jobs_data.format_job_duration_label(2.25) == "2.25hr"
    assert daily_jobs_data.format_job_duration_label(6) == "6hr"
    assert daily_jobs_data.format_job_duration_label(None) == ""

    move_date = "2026-12-21"
    _create_booking("Job One", move_date, "08:00", "10:00", crew="Yasu")
    _create_booking("Job Two", move_date, "11:35", "17:35", crew="Yasu")
    _create_booking("Job Three", move_date, "08:15", "10:45", crew="Yasu")
    _create_booking("Job Four", move_date, "08:00", "10:15", crew="Yasu")
    # Stored duration_hours is 4 in helper; display must still use Start/Finish.
    daily = daily_jobs_data.build_daily_jobs(move_date)
    by_name = {job["customer_name"]: job for job in daily["jobs"]}
    assert by_name["Job One"]["time_range"] == "8:00 AM – 10:00 AM"
    assert by_name["Job One"]["duration_label"] == "2hr"
    assert by_name["Job Two"]["time_range"] == "11:35 AM – 5:35 PM"
    assert by_name["Job Two"]["duration_label"] == "6hr"
    assert by_name["Job Three"]["time_range"] == "8:15 AM – 10:45 AM"
    assert by_name["Job Three"]["duration_label"] == "2.5hr"
    assert by_name["Job Four"]["time_range"] == "8:00 AM – 10:15 AM"
    assert by_name["Job Four"]["duration_label"] == "2.25hr"
    return True


def test_daily_jobs_page_shows_duration_beside_times():
    move_date = "2026-12-22"
    _create_booking("Side By Side", move_date, "08:00", "10:00")
    client = _login_client()
    html = client.get("/calendar/daily/{0}".format(move_date)).get_data(as_text=True)
    assert 'class="daily-job-time-range"' in html
    assert "8:00 AM – 10:00 AM" in html
    assert 'class="daily-job-duration">2hr</span>' in html
    css = (ROOT / "static" / "daily_jobs.css").read_text()
    time_block = css.split(".daily-job-time {")[1][:400]
    assert "flex" in time_block
    assert "nowrap" in time_block
    assert ".daily-job-duration" in css
    return True


def test_callout_hours_from_fee_and_rate():
    assert daily_jobs_data.callout_hours_from_booking(
        {"callout_fee": 90, "hourly_rate": 180}
    ) == 0.5
    assert daily_jobs_data.callout_hours_from_booking(
        {"callout_fee": 180, "hourly_rate": 180}
    ) == 1
    assert daily_jobs_data.callout_hours_from_booking(
        {"callout_fee": 0, "hourly_rate": 180}
    ) is None
    assert daily_jobs_data.callout_hours_from_booking(
        {"callout_fee": 90, "hourly_rate": 0}
    ) is None
    assert daily_jobs_data.format_callout_hours_label(0.5) == "+ 0.5hr call out"
    assert daily_jobs_data.format_callout_hours_label(1) == "+ 1hr call out"
    assert daily_jobs_data.format_callout_hours_label(None) == ""
    assert daily_jobs_data.format_callout_hours_label(0) == ""
    return True


def test_daily_jobs_shows_callout_hours_and_total_paid_hours():
    move_date = "2026-12-23"
    _create_booking(
        "Job One",
        move_date,
        "08:00",
        "11:00",
        hourly_rate=180.0,
        callout_fee=90.0,
    )
    _create_booking(
        "Job Two",
        move_date,
        "12:00",
        "14:45",
        hourly_rate=180.0,
        callout_fee=90.0,
    )

    daily = daily_jobs_data.build_daily_jobs(move_date)
    by_name = {job["customer_name"]: job for job in daily["jobs"]}
    assert by_name["Job One"]["duration_label"] == "3hr"
    assert by_name["Job One"]["callout_hours_label"] == "+ 0.5hr call out"
    assert by_name["Job Two"]["duration_label"] == "2.75hr"
    assert by_name["Job Two"]["callout_hours_label"] == "+ 0.5hr call out"
    assert daily["summary"]["total_paid_hours"] == 6.75
    assert daily["summary"]["total_paid_hours_label"] == "6.75hr"

    zero_date = "2026-12-24"
    _create_booking(
        "Job Zero Callout",
        zero_date,
        "16:00",
        "17:00",
        hourly_rate=180.0,
        callout_fee=0.0,
    )
    zero_daily = daily_jobs_data.build_daily_jobs(zero_date)
    assert zero_daily["jobs"][0]["duration_label"] == "1hr"
    assert zero_daily["jobs"][0]["callout_hours_label"] == ""
    assert zero_daily["summary"]["total_paid_hours_label"] == "1hr"

    client = _login_client()
    html = client.get("/calendar/daily/{0}".format(move_date)).get_data(as_text=True)
    assert "Total paid hours" in html
    assert "6.75hr" in html
    assert "+ 0.5hr call out" in html
    assert 'class="daily-job-callout"' in html
    assert html.count("+ 0.5hr call out") == 2
    zero_html = client.get("/calendar/daily/{0}".format(zero_date)).get_data(as_text=True)
    assert "+ 0.5hr call out" not in zero_html
    css = (ROOT / "static" / "daily_jobs.css").read_text()
    hours_block = css.split(".daily-job-hours {")[1][:300]
    assert "flex-direction: column" in hours_block
    assert "flex-end" in hours_block
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
        test_daily_jobs_duration_from_start_finish_times,
        test_daily_jobs_page_shows_duration_beside_times,
        test_callout_hours_from_fee_and_rate,
        test_daily_jobs_shows_callout_hours_and_total_paid_hours,
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
