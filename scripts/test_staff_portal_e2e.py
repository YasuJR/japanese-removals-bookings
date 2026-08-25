#!/usr/bin/env python3
"""E2E tests — Staff Portal at /staff."""

import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

# Independent from office/admin bootstrap password (STAFF_PASSWORD).
TEST_STAFF_PORTAL_PASSWORD = "jr-staff-portal-test-only"
os.environ["STAFF_PORTAL_PASSWORD"] = TEST_STAFF_PORTAL_PASSWORD

import auth
import database as db
import staff_auth
from app import app
from dashboard_data import perth_today, week_range

DESKTOP_PRIMARY = [
    "Home",
    "Dashboard",
    "Calendar",
    "New",
    "Crew",
    "Search",
    "Settings",
    "Log out",
]
DESKTOP_REMOVED = [
    "Driver",
    "Invoices",
    "Executive",
    "Profit",
    "Automation",
    "Checklist",
    "Leads",
    "Upcoming",
    "All",
    "Export CSV",
]


_user_n = 0
_book_n = 0

FORBIDDEN_LABELS = (
    "Revenue",
    "Hourly Rate",
    "Hourly rate",
    "Call Out Fee",
    "Callout fee",
    "Invoice Amount",
    "Invoice #",
    "Staff Cost",
    "Fuel Cost",
    "Other Cost",
    "Profit Margin",
    "Estimated Profit",
    "Analyse",
    "Analyze",
)


def _unique(prefix):
    global _book_n
    _book_n += 1
    return "{0}-{1}-{2}".format(prefix, os.getpid(), _book_n)


def _admin_client():
    global _user_n
    _user_n += 1
    db.init_db()
    uid = db.create_staff_user(
        "staff-portal-admin-{0}-{1}".format(os.getpid(), _user_n),
        auth.hash_password("office-admin-test-password"),
        "Office Admin Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _staff_client():
    db.init_db()
    client = app.test_client()
    response = client.post(
        "/staff/login",
        data={"password": TEST_STAFF_PORTAL_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/staff" in (response.headers.get("Location") or "")
    return client


def _create_job(
    customer,
    move_date,
    *,
    start_time="08:00",
    finish_time="12:00",
    crew="Yasu",
    status="Confirmed",
    phone="0412000456",
    pickup="12 Test St, Cannington WA 6107",
    dropoff="8 River Ave, Como WA 6152",
    notes="Apartment level 3. Lift booked 8:00–12:00.",
    hourly_rate=185.0,
    callout_fee=90.0,
):
    db.init_db()
    booking_id = db.create_booking(
        customer,
        phone,
        "staff-portal@example.com",
        pickup,
        dropoff,
        move_date,
        2,
        notes,
        start_time=start_time,
        finish_time=finish_time,
        duration_hours="4",
        status=status,
        crew=crew,
        hourly_rate=hourly_rate,
        callout_fee=callout_fee,
        payment_status="Unpaid",
    )
    db.update_booking_invoice_fields(
        booking_id,
        {
            "invoice_number": "INV-LEAK-{0}".format(booking_id),
            "payment_status": "Unpaid",
        },
    )
    db.update_booking_profit_fields(
        booking_id,
        {
            "staff_cost": 9876.54,
            "fuel_cost": 5432.10,
            "other_costs": 1111.11,
            "estimated_profit": 2222.22,
            "profit_margin_percent": 77.7,
        },
    )
    return booking_id


def _later_this_week(today):
    monday, sunday = week_range(today)
    for offset in (3, 4, 5, 2, 6):
        candidate = today + timedelta(days=offset)
        if monday <= candidate <= sunday and candidate != today + timedelta(days=1):
            return candidate
    return sunday


def test_staff_requires_login():
    client = app.test_client()
    response = client.get("/staff", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers.get("Location") or ""
    assert "/staff/login" in location
    return True


def test_staff_page_defaults_to_today_and_shows_assigned_jobs():
    today = perth_today().isoformat()
    customer = _unique("StaffPortal Tanaka")
    _create_job(customer, today, crew="Yasu,Ken")
    client = _staff_client()
    html = client.get("/staff").get_data(as_text=True)

    assert "Staff Portal" in html
    assert 'class="staff-portal-tab active"' in html
    assert ">Today</a>" in html
    assert "Tomorrow" in html
    assert "This Week" in html
    assert customer in html
    assert "8:00 AM" in html
    assert "Cannington" in html
    assert "Como" in html
    assert "Yasu / Ken" in html
    assert "4hr" in html
    assert "0412000456" in html
    assert "Apartment level 3. Lift booked 8:00–12:00." in html
    assert "Call Customer" in html
    assert "Text Customer" in html
    assert "Pickup Map" in html
    assert "Drop-off Map" in html
    assert 'href="tel:+61412000456"' in html
    assert 'href="sms:0412000456"' in html
    assert "https://maps.apple.com/?q=" in html
    assert quote("12 Test St, Cannington WA 6107") in html
    assert quote("8 River Ave, Como WA 6152") in html
    assert 'name="staff"' in html
    assert "START JOB" in html
    return True


def test_staff_filter_shows_only_selected_crew_jobs():
    today = perth_today().isoformat()
    yasu_customer = _unique("YasuOnly")
    ken_customer = _unique("KenOnly")
    both_customer = _unique("BothCrew")
    _create_job(yasu_customer, today, crew="Yasu", start_time="08:00")
    _create_job(ken_customer, today, crew="Ken", start_time="09:00")
    _create_job(both_customer, today, crew="Yasu,Ken", start_time="10:00")

    client = _staff_client()
    yasu_html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    ken_html = client.get("/staff?staff=Ken&range=today").get_data(as_text=True)

    assert yasu_customer in yasu_html
    assert both_customer in yasu_html
    assert ken_customer not in yasu_html

    assert ken_customer in ken_html
    assert both_customer in ken_html
    assert yasu_customer not in ken_html
    return True


def test_today_and_tomorrow_hide_completed_and_cancelled():
    today = perth_today()
    tomorrow = today + timedelta(days=1)
    live = _unique("LiveToday")
    done = _unique("CompletedToday")
    cancelled = _unique("CancelledToday")
    tomorrow_live = _unique("LiveTomorrow")
    tomorrow_done = _unique("CompletedTomorrow")
    _create_job(live, today.isoformat(), crew="Yasu", status="Confirmed")
    _create_job(done, today.isoformat(), crew="Yasu", status="Completed")
    _create_job(cancelled, today.isoformat(), crew="Yasu", status="Cancelled")
    _create_job(tomorrow_live, tomorrow.isoformat(), crew="Yasu", status="Confirmed")
    _create_job(tomorrow_done, tomorrow.isoformat(), crew="Yasu", status="Completed")

    client = _staff_client()
    today_html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    tomorrow_html = client.get("/staff?staff=Yasu&range=tomorrow").get_data(as_text=True)

    assert live in today_html
    assert done not in today_html
    assert cancelled not in today_html
    assert tomorrow_live not in today_html

    assert tomorrow_live in tomorrow_html
    assert tomorrow_done not in tomorrow_html
    assert live not in tomorrow_html
    return True


def test_this_week_tab_includes_later_week_jobs():
    today = perth_today()
    later = _later_this_week(today)
    customer = _unique("WeekOnly")
    _create_job(customer, later.isoformat(), crew="Yasu", status="Confirmed")

    client = _staff_client()
    today_html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    week_html = client.get("/staff?staff=Yasu&range=week").get_data(as_text=True)

    if later != today:
        assert customer not in today_html
    assert customer in week_html
    assert "staff-portal-tab active" in week_html
    assert ">This Week</a>" in week_html
    for heading in (
        "MONDAY",
        "TUESDAY",
        "WEDNESDAY",
        "THURSDAY",
        "FRIDAY",
        "SATURDAY",
        "SUNDAY",
    ):
        assert heading in week_html
    assert "No Jobs" in week_html
    return True


def test_staff_portal_hides_financial_data_and_booking_admin_links():
    today = perth_today().isoformat()
    customer = _unique("NoMoney")
    booking_id = _create_job(customer, today, crew="Yasu")
    client = _staff_client()
    html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)

    assert customer in html
    for label in FORBIDDEN_LABELS:
        assert label not in html, label
    assert "INV-LEAK-{0}".format(booking_id) not in html
    assert "9876.54" not in html
    assert "5432.10" not in html
    assert "1111.11" not in html
    assert "2222.22" not in html
    assert "77.7" not in html
    assert "$185" not in html
    assert "$90" not in html
    assert 'href="/bookings/{0}"'.format(booking_id) not in html
    assert 'href="/dashboard"' not in html
    assert "Unpaid" not in html
    return True


def test_staff_portal_reflects_booking_updates():
    today = perth_today().isoformat()
    customer = _unique("UpdatedCrew")
    booking_id = _create_job(
        customer,
        today,
        crew="Yasu",
        pickup="1 Old Pickup Rd, Perth WA 6000",
        notes="Original notes",
    )
    client = _staff_client()
    before = client.get("/staff?staff=Ken&range=today").get_data(as_text=True)
    assert customer not in before

    row = dict(db.get_booking(booking_id))
    db.update_booking(
        booking_id,
        row["customer_name"],
        row["phone"],
        row["email"],
        "99 New Pickup St, Cannington WA 6107",
        row["delivery_address"],
        row["move_date"],
        row["num_movers"],
        "Updated lift window",
        start_time=row["start_time"],
        finish_time=row["finish_time"],
        duration_hours=row["duration_hours"],
        crew="Ken",
        hourly_rate=row["hourly_rate"],
        callout_fee=row["callout_fee"],
        gst_enabled=row["gst_enabled"],
        payment_status=row["payment_status"],
        invoice_status=row["invoice_status"],
        status=row["status"],
    )

    ken_html = client.get("/staff?staff=Ken&range=today").get_data(as_text=True)
    yasu_html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    assert customer in ken_html
    assert "99 New Pickup St, Cannington WA 6107" in ken_html
    assert "Updated lift window" in ken_html
    assert customer not in yasu_html
    return True


def test_admin_desktop_nav_unchanged():
    client = _admin_client()
    html = client.get("/dashboard").get_data(as_text=True)
    import re

    match = re.search(
        r'<nav class="main-nav main-nav-desktop"[^>]*>(.*?)</nav>', html, re.S
    )
    assert match, "Missing desktop nav"
    labels = [label.strip() for label in re.findall(r">([^<]+)</a>", match.group(1))]
    normalized = [
        "Log out" if label.startswith("Log out") else label for label in labels
    ]
    assert normalized == DESKTOP_PRIMARY, normalized
    for item in DESKTOP_REMOVED:
        assert item not in normalized, item
    assert "Staff" not in normalized
    return True


def test_staff_login_uses_portal_password_not_admin_session():
    client = _staff_client()
    with client.session_transaction() as sess:
        assert sess.get("user_id") is None
        assert sess.get("username") is None
    html = client.get("/staff").get_data(as_text=True)
    assert "Staff Portal" in html
    assert TEST_STAFF_PORTAL_PASSWORD not in html
    assert 'href="/staff/logout"' in html
    assert "/logout\"" not in html.replace("/staff/logout", "")
    login_html = app.test_client().get("/staff/login").get_data(as_text=True)
    assert TEST_STAFF_PORTAL_PASSWORD not in login_html
    assert 'name="password"' in login_html
    return True


def test_admin_session_cannot_open_staff_portal():
    client = _admin_client()
    response = client.get("/staff", follow_redirects=False)
    assert response.status_code == 302
    assert "/staff/login" in (response.headers.get("Location") or "")
    return True


def test_staff_session_cannot_open_admin_pages():
    client = _staff_client()
    admin_paths = (
        "/",
        "/dashboard",
        "/calendar",
        "/invoices",
        "/profit",
        "/executive",
        "/bookings/new",
        "/bookings/all",
        "/bookings/search",
        "/driver",
        "/settings",
    )
    for path in admin_paths:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302, path
        location = response.headers.get("Location") or ""
        assert "/login" in location, path
        assert "/staff/login" not in location, path
    return True


def test_staff_logout_is_separate_from_admin_logout():
    staff = _staff_client()
    logged_out = staff.get("/staff/logout", follow_redirects=False)
    assert logged_out.status_code == 302
    assert "/staff/login" in (logged_out.headers.get("Location") or "")
    blocked = staff.get("/staff", follow_redirects=False)
    assert blocked.status_code == 302
    assert "/staff/login" in (blocked.headers.get("Location") or "")

    admin = _admin_client()
    admin_out = admin.get("/logout", follow_redirects=False)
    assert admin_out.status_code == 302
    location = admin_out.headers.get("Location") or ""
    assert location.endswith("/login") or location.rstrip("/").endswith("/login")
    assert "/staff/login" not in location
    return True


def test_wrong_password_and_admin_password_rejected():
    client = app.test_client()
    wrong = client.post(
        "/staff/login",
        data={"password": "office-admin-test-password"},
        follow_redirects=False,
    )
    assert wrong.status_code == 200
    html = wrong.get_data(as_text=True)
    assert "Invalid password." in html
    blocked = client.get("/staff", follow_redirects=False)
    assert blocked.status_code == 302
    assert "/staff/login" in (blocked.headers.get("Location") or "")

    previous_admin = os.environ.get("STAFF_PASSWORD")
    os.environ["STAFF_PASSWORD"] = TEST_STAFF_PORTAL_PASSWORD
    try:
        assert staff_auth.verify_staff_password(TEST_STAFF_PORTAL_PASSWORD) is False
    finally:
        if previous_admin is None:
            os.environ.pop("STAFF_PASSWORD", None)
        else:
            os.environ["STAFF_PASSWORD"] = previous_admin
    assert staff_auth.verify_staff_password(TEST_STAFF_PORTAL_PASSWORD) is True
    return True


def test_office_login_still_works_and_does_not_open_staff_portal():
    db.init_db()
    username = "office-login-{0}-{1}".format(os.getpid(), _user_n)
    password = "office-admin-test-password"
    db.create_staff_user(username, auth.hash_password(password), "Office Login Test")
    client = app.test_client()
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("user_id")
        assert sess.get("username") == username
    dashboard = client.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code == 200
    staff = client.get("/staff", follow_redirects=False)
    assert staff.status_code == 302
    assert "/staff/login" in (staff.headers.get("Location") or "")
    return True


def test_start_and_finish_job_saved_on_server_not_browser():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import staff_job_times

    today = perth_today().isoformat()
    customer = _unique("ActualTimes")
    booking_id = _create_job(
        customer, today, crew="Yasu", start_time="08:00", finish_time="12:00"
    )
    perth = ZoneInfo("Australia/Perth")
    started = staff_job_times.start_job(
        booking_id, "Yasu", now=datetime(2026, 8, 25, 8, 7, tzinfo=perth)
    )
    assert started is True
    row = dict(db.get_booking(booking_id))
    assert row["start_time"] in ("08:00", "8:00")
    assert row["finish_time"] in ("12:00", "12:00")
    assert row["duration_hours"] == "4"
    first_start = row["actual_start_time"]
    assert "T08:07:00" in first_start

    again = staff_job_times.start_job(
        booking_id, "Yasu", now=datetime(2026, 8, 25, 9, 0, tzinfo=perth)
    )
    assert again is False
    row = dict(db.get_booking(booking_id))
    assert row["actual_start_time"] == first_start

    assert staff_job_times.finish_job(booking_id, "Ken") is False
    finished = staff_job_times.finish_job(
        booking_id, "Yasu", now=datetime(2026, 8, 25, 11, 42, tzinfo=perth)
    )
    assert finished is True
    row = dict(db.get_booking(booking_id))
    assert "T11:42:00" in row["actual_finish_time"]
    assert int(row["actual_duration"]) == 215
    assert staff_job_times.format_worked_duration(row["actual_duration"]) == "3hr 35min"
    assert staff_job_times.finish_job(
        booking_id, "Yasu", now=datetime(2026, 8, 25, 12, 0, tzinfo=perth)
    ) is False
    row = dict(db.get_booking(booking_id))
    assert "T11:42:00" in row["actual_finish_time"]
    assert int(row["actual_duration"]) == 215
    assert row["start_time"] in ("08:00", "8:00")
    return True


def test_staff_portal_start_finish_buttons_and_confirm():
    today = perth_today().isoformat()
    customer = _unique("StartFinishUi")
    booking_id = _create_job(customer, today, crew="Yasu")
    client = _staff_client()
    html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    assert "START JOB" in html
    assert "/staff/jobs/{0}/start".format(booking_id) in html
    assert customer in html

    started = client.post(
        "/staff/jobs/{0}/start".format(booking_id),
        data={"staff": "Yasu", "range": "today"},
        follow_redirects=False,
    )
    assert started.status_code == 302
    html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    assert "Started:" in html
    assert "/staff/jobs/{0}/start".format(booking_id) not in html
    assert "/staff/jobs/{0}/finish".format(booking_id) in html
    row = dict(db.get_booking(booking_id))
    assert row["actual_start_time"]
    assert not row["actual_finish_time"]

    confirm = client.get(
        "/staff/jobs/{0}/finish?staff=Yasu&range=today".format(booking_id)
    )
    confirm_html = confirm.get_data(as_text=True)
    assert confirm.status_code == 200
    assert "Finish this job?" in confirm_html
    assert customer in confirm_html
    assert "Yes, finish" in confirm_html

    finished = client.post(
        "/staff/jobs/{0}/finish".format(booking_id),
        data={"staff": "Yasu", "range": "today"},
        follow_redirects=False,
    )
    assert finished.status_code == 302
    html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    assert "Started:" in html
    assert "Finished:" in html
    assert "Worked:" in html
    assert "/staff/jobs/{0}/start".format(booking_id) not in html
    assert "/staff/jobs/{0}/finish".format(booking_id) not in html
    row = dict(db.get_booking(booking_id))
    assert row["actual_finish_time"]
    assert row["actual_duration"] is not None
    return True


def test_cannot_finish_before_start_or_start_other_crew_job():
    today = perth_today().isoformat()
    yasu_job = _create_job(_unique("YasuStartOnly"), today, crew="Yasu")
    ken_job = _create_job(_unique("KenStartOnly"), today, crew="Ken")
    client = _staff_client()
    finish_first = client.post(
        "/staff/jobs/{0}/finish".format(yasu_job),
        data={"staff": "Yasu", "range": "today"},
        follow_redirects=False,
    )
    assert finish_first.status_code in (302, 200)
    row = dict(db.get_booking(yasu_job))
    assert not row.get("actual_finish_time")

    other = client.post(
        "/staff/jobs/{0}/start".format(ken_job),
        data={"staff": "Yasu", "range": "today"},
        follow_redirects=False,
    )
    assert other.status_code == 302
    row = dict(db.get_booking(ken_job))
    assert not row.get("actual_start_time")
    return True


def test_weekly_schedule_shows_completed_not_cancelled():
    today = perth_today()
    later = _later_this_week(today)
    live = _unique("WeekLive")
    done = _unique("WeekDone")
    cancelled = _unique("WeekCancelled")
    _create_job(live, later.isoformat(), crew="Yasu", status="Confirmed")
    _create_job(done, later.isoformat(), crew="Yasu", status="Completed")
    _create_job(cancelled, later.isoformat(), crew="Yasu", status="Cancelled")
    client = _staff_client()
    week_html = client.get("/staff?staff=Yasu&range=week").get_data(as_text=True)
    today_html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    assert live in week_html
    assert done in week_html
    assert "Completed" in week_html
    assert cancelled not in week_html
    if later != today:
        assert done not in today_html
    assert "Cannington → Como" in week_html or "Cannington" in week_html
    assert "START JOB" in week_html
    return True


def test_guest_cannot_start_job():
    today = perth_today().isoformat()
    booking_id = _create_job(_unique("GuestStart"), today, crew="Yasu")
    client = app.test_client()
    response = client.post(
        "/staff/jobs/{0}/start".format(booking_id),
        data={"staff": "Yasu", "range": "today"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/staff/login" in (response.headers.get("Location") or "")
    row = dict(db.get_booking(booking_id))
    assert not row.get("actual_start_time")
    return True


def main():
    tests = [
        test_staff_requires_login,
        test_staff_page_defaults_to_today_and_shows_assigned_jobs,
        test_staff_filter_shows_only_selected_crew_jobs,
        test_today_and_tomorrow_hide_completed_and_cancelled,
        test_this_week_tab_includes_later_week_jobs,
        test_staff_portal_hides_financial_data_and_booking_admin_links,
        test_staff_portal_reflects_booking_updates,
        test_admin_desktop_nav_unchanged,
        test_staff_login_uses_portal_password_not_admin_session,
        test_admin_session_cannot_open_staff_portal,
        test_staff_session_cannot_open_admin_pages,
        test_staff_logout_is_separate_from_admin_logout,
        test_wrong_password_and_admin_password_rejected,
        test_office_login_still_works_and_does_not_open_staff_portal,
        test_start_and_finish_job_saved_on_server_not_browser,
        test_staff_portal_start_finish_buttons_and_confirm,
        test_cannot_finish_before_start_or_start_other_crew_job,
        test_weekly_schedule_shows_completed_not_cancelled,
        test_guest_cannot_start_job,
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
