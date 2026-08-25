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

import auth
import database as db
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


def _login_client():
    global _user_n
    _user_n += 1
    db.init_db()
    uid = db.create_staff_user(
        "staff-portal-{0}-{1}".format(os.getpid(), _user_n),
        auth.hash_password("test"),
        "Staff Portal Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
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
    assert "/login" in (response.headers.get("Location") or "")
    return True


def test_staff_page_defaults_to_today_and_shows_assigned_jobs():
    today = perth_today().isoformat()
    customer = _unique("StaffPortal Tanaka")
    _create_job(customer, today, crew="Yasu,Ken")
    client = _login_client()
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
    return True


def test_staff_filter_shows_only_selected_crew_jobs():
    today = perth_today().isoformat()
    yasu_customer = _unique("YasuOnly")
    ken_customer = _unique("KenOnly")
    both_customer = _unique("BothCrew")
    _create_job(yasu_customer, today, crew="Yasu", start_time="08:00")
    _create_job(ken_customer, today, crew="Ken", start_time="09:00")
    _create_job(both_customer, today, crew="Yasu,Ken", start_time="10:00")

    client = _login_client()
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

    client = _login_client()
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

    client = _login_client()
    today_html = client.get("/staff?staff=Yasu&range=today").get_data(as_text=True)
    week_html = client.get("/staff?staff=Yasu&range=week").get_data(as_text=True)

    if later != today:
        assert customer not in today_html
    assert customer in week_html
    assert "staff-portal-tab active" in week_html
    assert ">This Week</a>" in week_html
    return True


def test_staff_portal_hides_financial_data_and_booking_admin_links():
    today = perth_today().isoformat()
    customer = _unique("NoMoney")
    booking_id = _create_job(customer, today, crew="Yasu")
    client = _login_client()
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
    client = _login_client()
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
    client = _login_client()
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
