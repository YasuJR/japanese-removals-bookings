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
    duration_hours="4",
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
        duration_hours=duration_hours,
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
    html = client.get("/staff?staff=Yasu").get_data(as_text=True)

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
    assert "START JOB" not in html
    assert "FINISH JOB" not in html
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


def test_today_shows_all_assigned_jobs_including_completed():
    """Today = selected staff in crew + that calendar date; Cancelled only is excluded."""
    from datetime import date as date_cls
    from staff_portal import build_staff_portal

    today = date_cls(2099, 1, 1) + timedelta(
        days=(os.getpid() + int(time.time() * 1000)) % 3000
    )
    tomorrow = today + timedelta(days=1)
    first = _unique("Justin")
    second = _unique("Senna Yao")
    third = _unique("Another Customer")
    cancelled = _unique("CancelledToday")
    ken_only = _unique("KenOnlyToday")
    tomorrow_live = _unique("LiveTomorrow")
    _create_job(first, today.isoformat(), crew="Yasu", start_time="08:00", status="Confirmed")
    completed_id = _create_job(
        second, today.isoformat(), crew="Yasu,Ken", start_time="15:00", status="Completed"
    )
    _create_job(third, today.isoformat(), crew="Yasu", start_time="17:00", status="Confirmed")
    _create_job(cancelled, today.isoformat(), crew="Yasu", start_time="09:00", status="Cancelled")
    _create_job(ken_only, today.isoformat(), crew="Ken", start_time="10:00", status="Confirmed")
    _create_job(tomorrow_live, tomorrow.isoformat(), crew="Yasu", start_time="08:00", status="Confirmed")
    db.save_booking_actual_times(completed_id, "15:10", "18:00", 170)

    portal = build_staff_portal("Yasu", "today", today)
    names = [job["customer_name"] for job in portal["jobs"]]
    assert names == [first, second, third]
    assert portal["job_count"] == 3
    assert portal["jobs_label"] == "3 Jobs Today"
    assert [job["start_time"] for job in portal["jobs"]] == [
        "8:00 AM",
        "3:00 PM",
        "5:00 PM",
    ]
    assert portal["jobs"][1]["status_display"] == "COMPLETED"
    assert portal["jobs"][1]["has_actual"] is True
    assert portal["jobs"][1]["actual_range_display"] == "3:10 PM – 6:00 PM"
    assert portal["jobs"][1]["worked_display"] == "2hr 50min"
    assert portal["jobs"][0]["has_actual"] is False
    assert portal["jobs"][2]["has_actual"] is False
    assert cancelled not in names
    assert ken_only not in names
    assert tomorrow_live not in names

    real_today = perth_today()
    live = _unique("HttpLive")
    done = _unique("HttpDone")
    cancelled_today = _unique("HttpCancelled")
    _create_job(live, real_today.isoformat(), crew="Yasu", start_time="08:00", status="Confirmed")
    _create_job(done, real_today.isoformat(), crew="Yasu", start_time="15:00", status="Completed")
    _create_job(
        cancelled_today, real_today.isoformat(), crew="Yasu", start_time="09:00", status="Cancelled"
    )
    client = _staff_client()
    today_html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    assert live in today_html
    assert done in today_html
    assert cancelled_today not in today_html
    assert today_html.find(live) < today_html.find(done)
    assert "COMPLETED" in today_html
    assert build_staff_portal("Yasu", "today", real_today)["jobs_label"] in today_html
    return True


def test_today_and_tomorrow_status_filters():
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
    assert done in today_html
    assert "COMPLETED" in today_html
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


def _edit_form(booking_id, **overrides):
    row = dict(db.get_booking(booking_id))
    form = {
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "email": row["email"],
        "pickup_address": row["pickup_address"],
        "delivery_address": row["delivery_address"],
        "move_date": row["move_date"],
        "num_movers": str(row["num_movers"]),
        "notes": row["notes"] or "",
        "start_time": row["start_time"] or "08:00",
        "finish_time": row["finish_time"] or "12:00",
        "duration_hours": row["duration_hours"] or "4",
        "hourly_rate": str(row["hourly_rate"] or 185),
        "callout_fee": str(row["callout_fee"] or 90),
        "gst_enabled": "on",
        "payment_status": row["payment_status"] or "Unpaid",
        "invoice_status": row["invoice_status"] or "",
        "status": row["status"] or "Confirmed",
        "action": "save",
        "double_booking_override_confirm": "on",
    }
    form.update(overrides)
    return form


def test_admin_saves_actual_times_without_changing_scheduled_times():
    import staff_job_times

    today = perth_today().isoformat()
    customer = _unique("AdminActual")
    booking_id = _create_job(
        customer, today, crew="Yasu", start_time="08:00", finish_time="12:00"
    )
    client = _admin_client()
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert 'name="actual_start_time"' in html
    assert 'name="actual_finish_time"' in html
    assert "Actual start time" in html
    assert "Actual finish time" in html
    assert "Actual duration" in html

    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_edit_form(
            booking_id,
            actual_start_time="08:10",
            actual_finish_time="11:45",
        ),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    row = dict(db.get_booking(booking_id))
    assert row["start_time"] in ("08:00", "8:00")
    assert row["finish_time"] in ("12:00",)
    assert row["duration_hours"] == "4"
    assert staff_job_times.parse_actual_clock(row["actual_start_time"]) == "08:10"
    assert staff_job_times.parse_actual_clock(row["actual_finish_time"]) == "11:45"
    assert int(row["actual_duration"]) == 215
    assert staff_job_times.format_worked_duration(row["actual_duration"]) == "3hr 35min"

    follow = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert 'value="08:10"' in follow
    assert 'value="11:45"' in follow
    assert "3hr 35min" in follow
    details = client.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    assert "Actual start" in details
    assert "8:10 AM" in details
    assert "11:45 AM" in details
    return True


def test_staff_portal_shows_actual_times_read_only():
    today = perth_today().isoformat()
    customer = _unique("StaffSeesActual")
    booking_id = _create_job(customer, today, crew="Yasu")
    db.save_booking_actual_times(booking_id, "08:10", "11:45", 215)
    staff = _staff_client()
    html = staff.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    assert customer in html
    assert "Scheduled" in html
    assert "Actual: 8:10 AM – 11:45 AM" in html
    assert "Worked: 3hr 35min" in html
    assert "START JOB" not in html
    assert "FINISH JOB" not in html
    assert "Finish this job?" not in html
    assert 'name="actual_start_time"' not in html
    assert "/staff/jobs/" not in html

    week_html = staff.get("/staff?staff=Yasu&range=week").get_data(as_text=True)
    assert "Actual: 8:10 AM – 11:45 AM" in week_html
    assert customer in week_html

    blocked = staff.post(
        "/staff/jobs/{0}/start".format(booking_id),
        data={"staff": "Yasu", "actual_start_time": "09:00"},
        follow_redirects=False,
    )
    assert blocked.status_code == 404
    blocked_finish = staff.post(
        "/staff/jobs/{0}/finish".format(booking_id),
        data={"staff": "Yasu", "actual_finish_time": "10:00"},
        follow_redirects=False,
    )
    assert blocked_finish.status_code == 404
    row = dict(db.get_booking(booking_id))
    assert row["actual_start_time"] in ("08:10",)
    assert row["actual_finish_time"] in ("11:45",)
    assert int(row["actual_duration"]) == 215

    staff_edit = staff.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_edit_form(
            booking_id,
            actual_start_time="09:99",
            actual_finish_time="18:00",
        ),
        follow_redirects=False,
    )
    assert staff_edit.status_code == 302
    assert "/login" in (staff_edit.headers.get("Location") or "")
    row = dict(db.get_booking(booking_id))
    assert row["actual_start_time"] in ("08:10",)
    return True


def test_staff_hides_actual_when_not_set():
    from staff_portal import build_staff_portal

    today = perth_today().isoformat()
    customer = _unique("NoActualYet")
    _create_job(customer, today, crew="Yasu")
    portal = build_staff_portal("Yasu", "today")
    job = [item for item in portal["jobs"] if item["customer_name"] == customer][0]
    assert job["has_actual"] is False
    assert not job["actual_range_display"]
    html = _staff_client().get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    assert customer in html
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
    assert "COMPLETED" in week_html
    assert cancelled not in week_html
    if later != today:
        assert done not in today_html
    assert "Cannington → Como" in week_html or "Cannington" in week_html
    assert "START JOB" not in week_html
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
    return True


def _set_actual(booking_id, start, finish, minutes):
    db.save_booking_actual_times(booking_id, start, finish, minutes)


def test_this_week_weekly_worked_hours():
    from datetime import date as date_cls
    from staff_portal import build_staff_portal

    monday = date_cls(2100, 1, 4)
    while monday.weekday() != 0:
        monday += timedelta(days=1)
    monday = monday + timedelta(weeks=(os.getpid() % 40) + 1)
    wednesday = monday + timedelta(days=2)
    sunday = monday + timedelta(days=6)
    previous_sunday = monday - timedelta(days=1)
    next_monday = monday + timedelta(days=7)

    _create_job(
        _unique("YasuMonA"), monday.isoformat(), crew="Yasu",
        start_time="08:00", finish_time="11:35", status="Completed",
    )
    _create_job(
        _unique("YasuMonB"), monday.isoformat(), crew="Yasu",
        start_time="13:00", finish_time="16:00", status="Confirmed",
    )
    _create_job(
        _unique("YasuTue"), (monday + timedelta(days=1)).isoformat(),
        crew="Yasu", start_time="08:00", finish_time="10:35", status="Completed",
    )
    _create_job(
        _unique("YasuWedNone"), (monday + timedelta(days=2)).isoformat(),
        crew="Yasu", start_time="08:00", finish_time="", status="Confirmed",
    )
    _create_job(
        _unique("YasuThuCancel"), (monday + timedelta(days=3)).isoformat(),
        crew="Yasu", start_time="08:00", finish_time="09:40", status="Cancelled",
    )
    _create_job(
        _unique("KenOnlyFri"), (monday + timedelta(days=4)).isoformat(),
        crew="Ken", start_time="08:00", finish_time="14:40", status="Completed",
    )
    _create_job(
        _unique("SharedThree"), (monday + timedelta(days=4)).isoformat(),
        crew="Yasu,Ken,Tom", start_time="13:00", finish_time="17:00", status="Completed",
    )
    _create_job(
        _unique("OutsidePrev"), previous_sunday.isoformat(),
        crew="Yasu", start_time="08:00", finish_time="09:30", status="Completed",
    )
    _create_job(
        _unique("OutsideNext"), next_monday.isoformat(),
        crew="Yasu", start_time="08:00", finish_time="09:30", status="Completed",
    )

    yasu = build_staff_portal("Yasu", "week", wednesday)
    ken = build_staff_portal("Ken", "week", wednesday)
    tom = build_staff_portal("Tom", "week", wednesday)

    assert yasu["start_date"] == monday.isoformat()
    assert yasu["end_date"] == sunday.isoformat()
    assert yasu["weekly_worked"]["staff"] == "Yasu"
    assert yasu["weekly_worked"]["minutes"] == 215 + 180 + 155 + 240
    assert yasu["weekly_worked"]["display"] == "13hr 10min"
    assert ken["weekly_worked"]["minutes"] == 400 + 240
    assert ken["weekly_worked"]["display"] == "10hr 40min"
    assert tom["weekly_worked"]["minutes"] == 240
    assert tom["weekly_worked"]["display"] == "4hr"

    by_heading = {day["heading"]: day for day in yasu["week_days"]}
    assert by_heading["MONDAY"]["worked_display"] == "6hr 35min"
    assert by_heading["TUESDAY"]["worked_display"] == "2hr 35min"
    assert by_heading["WEDNESDAY"]["worked_display"] == "0hr"
    assert by_heading["THURSDAY"]["worked_display"] == "0hr"
    assert by_heading["FRIDAY"]["worked_display"] == "4hr"

    today_portal = build_staff_portal("Yasu", "today", wednesday)
    tomorrow_portal = build_staff_portal("Yasu", "tomorrow", wednesday)
    assert today_portal["weekly_worked"] is None
    assert tomorrow_portal["weekly_worked"] is None

    client = _staff_client()
    live_week = client.get("/staff?staff=Yasu&range=week").get_data(as_text=True)
    live_today = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    live_tomorrow = client.get("/staff?staff=Yasu&range=tomorrow").get_data(as_text=True)
    assert "WEEKLY WORKED" in live_week
    assert "WEEKLY WORKED" not in live_today
    assert "WEEKLY WORKED" not in live_tomorrow
    assert "9.17hr" not in live_week
    assert "Hourly Rate" not in live_week
    assert "Staff Cost" not in live_week
    return True


def test_weekly_worked_format_examples():
    import staff_job_times

    assert staff_job_times.format_weekly_worked(550) == "9hr 10min"
    assert staff_job_times.format_weekly_worked(1880) == "31hr 20min"
    assert staff_job_times.format_weekly_worked(0) == "0hr"
    assert staff_job_times.format_weekly_worked(None) == "0hr"
    assert staff_job_times.parse_actual_clock("8:00 AM") == "08:00"
    assert staff_job_times.parse_actual_clock("10:30 AM") == "10:30"
    assert staff_job_times.parse_actual_clock("1:00 PM") == "13:00"
    assert staff_job_times.duration_minutes_between("8:00 AM", "10:30 AM") == 150
    assert staff_job_times.format_weekly_worked(150) == "2hr 30min"
    assert staff_job_times.format_hours_as_worked(2.5) == "2hr 30min"
    assert staff_job_times.format_hours_as_worked("2.5hr") == "2hr 30min"
    assert staff_job_times.format_hours_as_worked("5.75") == "5hr 45min"
    assert staff_job_times.format_hours_as_worked("4.0") == "4hr"
    assert staff_job_times.format_hours_as_worked(4) == "4hr"
    return True


def test_weekly_worked_rebecca_style_completed_uses_owner_start_finish():
    """Completed Keiichi/Yasu job with empty actual_* must use start_time/finish_time."""
    from datetime import date as date_cls
    from staff_portal import build_staff_portal

    names = [row["name"] for row in db.list_crew_members(active_only=False)]
    if "Keiichi" not in names:
        db.create_crew_member("Keiichi", role="Driver", active=1)

    monday = date_cls(2101, 1, 3)
    while monday.weekday() != 0:
        monday += timedelta(days=1)
    monday = monday + timedelta(
        weeks=((os.getpid() + int(time.time() * 1000)) % 400) + 2
    )

    rebecca = _create_job(
        "Rebecca Boyce",
        monday.isoformat(),
        crew="Keiichi,Yasu",
        start_time="08:00",
        finish_time="10:30",
        status="Completed",
    )
    afternoon = _create_job(
        _unique("SameDayAfternoon"),
        monday.isoformat(),
        crew="Yasu",
        start_time="13:00",
        finish_time="16:15",
        status="Completed",
    )
    _create_job(
        _unique("StartOnly"),
        (monday + timedelta(days=1)).isoformat(),
        crew="Yasu",
        start_time="08:00",
        finish_time="",
        status="Completed",
    )
    row = dict(db.get_booking(rebecca))
    assert row["start_time"] in ("08:00", "8:00")
    assert row["finish_time"] in ("10:30",)
    assert not (row.get("actual_start_time") or "").strip()
    assert not (row.get("actual_finish_time") or "").strip()
    assert row.get("actual_duration") in (None, "", 0)

    yasu = build_staff_portal("Yasu", "week", monday)
    keiichi = build_staff_portal("Keiichi", "week", monday)
    ken = build_staff_portal("Ken", "week", monday)
    by_heading = {day["heading"]: day for day in yasu["week_days"]}

    assert yasu["weekly_worked"]["display"] != "0hr"
    assert yasu["weekly_worked"]["minutes"] == 150 + 195
    assert yasu["weekly_worked"]["display"] == "5hr 45min"
    assert by_heading["MONDAY"]["worked_display"] == "5hr 45min"
    assert keiichi["weekly_worked"]["minutes"] == 150
    assert keiichi["weekly_worked"]["display"] == "2hr 30min"
    assert rebecca in [job["id"] for job in keiichi["jobs"]]
    assert rebecca not in [job["id"] for job in ken["jobs"]]
    rebecca_job = [job for job in yasu["jobs"] if job["id"] == rebecca][0]
    assert rebecca_job["owner_start_hm"] == "08:00"
    assert rebecca_job["owner_finish_hm"] == "10:30"
    assert afternoon
    return True


def test_job_card_shows_estimated_and_actual_worked():
    from datetime import date as date_cls
    from staff_portal import build_staff_portal

    names = [row["name"] for row in db.list_crew_members(active_only=False)]
    if "Keiichi" not in names:
        db.create_crew_member("Keiichi", role="Driver", active=1)

    monday = date_cls(2102, 1, 6)
    while monday.weekday() != 0:
        monday += timedelta(days=1)
    monday = monday + timedelta(
        weeks=((os.getpid() + int(time.time() * 1000)) % 400) + 20
    )
    customer = "Rebecca Boyce"
    booking_id = _create_job(
        customer,
        monday.isoformat(),
        crew="Keiichi,Yasu",
        start_time="08:00",
        finish_time="12:30",
        duration_hours="2.5",
        status="Completed",
    )
    unset = _create_job(
        _unique("NoFinishYet"),
        monday.isoformat(),
        crew="Yasu",
        start_time="13:00",
        finish_time="",
        duration_hours="5.75",
        status="Confirmed",
    )
    row = dict(db.get_booking(booking_id))
    assert str(row["duration_hours"]) == "2.5"
    assert row["start_time"] in ("08:00", "8:00")
    assert row["finish_time"] in ("12:30",)

    portal = build_staff_portal("Yasu", "week", monday)
    job = [item for item in portal["jobs"] if item["id"] == booking_id][0]
    unset_job = [item for item in portal["jobs"] if item["id"] == unset][0]
    assert job["estimated_duration"] == "2hr 30min"
    assert job["actual_worked_display"] == "4hr 30min"
    assert unset_job["estimated_duration"] == "5hr 45min"
    assert unset_job["actual_worked_display"] == "Not set"
    assert portal["weekly_worked"]["minutes"] == 270
    assert portal["weekly_worked"]["display"] == "4hr 30min"

    keiichi = build_staff_portal("Keiichi", "week", monday)
    assert keiichi["weekly_worked"]["display"] == "4hr 30min"

    today = perth_today()
    tomorrow = today + timedelta(days=1)
    live_today = _unique("CardToday")
    live_tomorrow = _unique("CardTomorrow")
    _create_job(
        live_today,
        today.isoformat(),
        crew="Yasu",
        start_time="08:00",
        finish_time="12:30",
        duration_hours="2.5",
        status="Completed",
    )
    _create_job(
        live_tomorrow,
        tomorrow.isoformat(),
        crew="Yasu",
        start_time="08:00",
        finish_time="12:30",
        duration_hours="2.5",
        status="Confirmed",
    )
    client = _staff_client()
    today_html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    tomorrow_html = client.get("/staff?staff=Yasu&range=tomorrow").get_data(as_text=True)
    week_html = client.get("/staff?staff=Yasu&range=week").get_data(as_text=True)
    today_snippet = today_html[today_html.find(live_today): today_html.find(live_today) + 1200]
    tomorrow_snippet = tomorrow_html[tomorrow_html.find(live_tomorrow): tomorrow_html.find(live_tomorrow) + 1200]
    week_snippet = week_html[week_html.find(live_today): week_html.find(live_today) + 1200]
    for snippet in (today_snippet, tomorrow_snippet, week_snippet):
        assert "Estimated" in snippet
        assert "Actual Worked" in snippet
        assert "2hr 30min" in snippet
        assert "4hr 30min" in snippet
        assert "2.5hr" not in snippet
    return True


def main():
    tests = [
        test_staff_requires_login,
        test_staff_page_defaults_to_today_and_shows_assigned_jobs,
        test_staff_filter_shows_only_selected_crew_jobs,
        test_today_shows_all_assigned_jobs_including_completed,
        test_today_and_tomorrow_status_filters,
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
        test_admin_saves_actual_times_without_changing_scheduled_times,
        test_staff_portal_shows_actual_times_read_only,
        test_staff_hides_actual_when_not_set,
        test_weekly_schedule_shows_completed_not_cancelled,
        test_this_week_weekly_worked_hours,
        test_weekly_worked_format_examples,
        test_weekly_worked_rebecca_style_completed_uses_owner_start_finish,
        test_job_card_shows_estimated_and_actual_worked,
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
