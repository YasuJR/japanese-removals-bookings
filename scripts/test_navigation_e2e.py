#!/usr/bin/env python3
"""E2E tests for simplified desktop and mobile navigation."""

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth
import database as db
from app import app

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

DESKTOP_PRIMARY = [
    "Home",
    "Dashboard",
    "Calendar",
    "New",
    "Crew",
    "Driver",
    "Invoices",
    "Search",
    "Settings",
    "Log out",
]
DESKTOP_REMOVED = [
    "Executive",
    "Profit",
    "Automation",
    "Checklist",
    "Leads",
    "Upcoming",
    "All",
    "Export CSV",
]
MOBILE_PRIMARY = ["Dashboard", "Calendar", "New", "Crew", "More"]
MOBILE_MORE = [
    "Home",
    "Driver",
    "Invoices",
    "Search",
    "Settings",
    "Executive",
    "Profit",
    "Automation",
    "Checklist",
    "Leads",
    "Upcoming",
    "All bookings",
    "Export CSV",
    "Log out",
]
SETTINGS_ADMIN_LINKS = [
    "Open Executive",
    "Open Profit",
    "Open Automation",
    "Export CSV",
    "Open Leads",
    "Upcoming list",
    "All bookings",
]
ROUTE_CHECKS = [
    ("/", "ceo_dashboard"),
    ("/dashboard", "dashboard"),
    ("/calendar", "booking_calendar"),
    ("/bookings/new", "new_booking"),
    ("/crew-schedule", "crew_schedule"),
    ("/driver", "driver"),
    ("/invoices", "outstanding_invoices"),
    ("/bookings/search", "search_bookings"),
    ("/settings", "settings"),
    ("/executive", "executive_dashboard"),
    ("/profit", "profit"),
    ("/automation", "automation_hub"),
    ("/daily-checklist", "daily_checklist"),
    ("/leads", "leads"),
    ("/bookings/upcoming", "upcoming"),
    ("/bookings/all", "all_bookings"),
]


def _unique_username(prefix: str) -> str:
    return "{0}-{1}-{2}".format(prefix, os.getpid(), time.time_ns())


def _admin_client():
    db.init_db()
    username = _unique_username("nav-admin")
    uid = db.create_staff_user(
        username,
        auth.hash_password("test-password"),
        "Nav Admin",
        is_admin=1,
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = username
    return client


def _extract_nav_block(html: str, class_name: str) -> str:
    pattern = r'<nav class="{0}"[^>]*>(.*?)</nav>'.format(re.escape(class_name))
    match = re.search(pattern, html, re.S)
    assert match, "Missing nav block: {0}".format(class_name)
    return match.group(1)


def _link_labels(block_html: str) -> list:
    return re.findall(r">([^<]+)</a>", block_html)


def test_desktop_nav_shows_primary_items_only():
    client = _admin_client()
    html = client.get("/dashboard").get_data(as_text=True)
    desktop = _extract_nav_block(html, "main-nav main-nav-desktop")
    labels = _link_labels(desktop)
    for item in DESKTOP_PRIMARY:
        assert any(item in label for label in labels), item
    for item in DESKTOP_REMOVED:
        assert not any(item == label.strip() for label in labels), item
    home_idx = next(i for i, label in enumerate(labels) if "Home" in label)
    logout_idx = next(i for i, label in enumerate(labels) if "Log out" in label)
    assert home_idx < logout_idx
    return True


def test_mobile_primary_and_more_menus():
    client = _admin_client()
    html = client.get("/dashboard").get_data(as_text=True)
    mobile = _extract_nav_block(html, "mobile-bottom-nav")
    for item in MOBILE_PRIMARY:
        assert item in mobile, item
    more_panel = re.search(r'id="mobile-more-panel"[^>]*>(.*?)</div>\s*<div class="mobile-more-backdrop"', html, re.S)
    assert more_panel, "Missing mobile more panel"
    for item in MOBILE_MORE:
        assert item in more_panel.group(1), item
    return True


def test_settings_admin_area_links():
    client = _admin_client()
    html = client.get("/settings").get_data(as_text=True)
    for label in SETTINGS_ADMIN_LINKS:
        assert label in html, label
    return True


def test_search_links_to_all_bookings():
    client = _admin_client()
    html = client.get("/bookings/search").get_data(as_text=True)
    assert "Browse all bookings" in html
    assert 'href="/bookings/all"' in html
    return True


def test_driver_links_to_daily_checklist():
    client = _admin_client()
    html = client.get("/driver").get_data(as_text=True)
    assert "Daily checklist" in html
    assert 'href="/daily-checklist' in html
    return True


def test_all_bookings_renders_postgres_created_at():
    """PostgreSQL returns created_at as datetime; All bookings must not 500."""
    from datetime import datetime

    class FakeRow:
        def __init__(self, data):
            self._data = data

        def __getitem__(self, key):
            return self._data[key]

        def keys(self):
            return self._data.keys()

        def get(self, key, default=None):
            return self._data.get(key, default)

    fake = FakeRow(
        {
            "id": 999,
            "customer_name": "Postgres Created At Test",
            "move_date": "2026-12-01",
            "start_time": "09:00",
            "finish_time": "11:00",
            "phone": "0400000000",
            "email": "",
            "pickup_address": "1 Test St",
            "delivery_address": "2 Test Ave",
            "num_movers": 2,
            "notes": "",
            "payment_status": "Unpaid",
            "status": "Confirmed",
            "crew": "",
            "created_at": datetime(2026, 8, 11, 10, 30, 0),
        }
    )

    original = db.list_all
    db.list_all = lambda: [fake]
    try:
        client = _admin_client()
        resp = client.get("/bookings/all")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200, resp.status_code
        assert "Postgres Created At Test" in html
        assert "2026-08-11" in html
    finally:
        db.list_all = original
    return True


def test_all_moved_routes_still_load():
    client = _admin_client()
    for path, _name in ROUTE_CHECKS:
        resp = client.get(path)
        assert resp.status_code == 200, path
    return True


def test_crew_page_has_schedule_and_management():
    client = _admin_client()
    html = client.get("/crew-schedule").get_data(as_text=True)
    assert "Crew schedule" in html
    assert 'id="crew-management"' in html
    assert "Edit" in html
    assert "Add crew member" in html
    return True


def test_existing_bookings_unchanged():
    db.init_db()
    before_count = len(db.list_all())
    if before_count:
        sample = dict(db.list_all()[0])
        client = _admin_client()
        client.get("/dashboard")
        client.get("/settings")
        after = dict(db.get_booking(sample["id"]))
        assert after["customer_name"] == sample["customer_name"]
        assert after["crew"] == sample["crew"]
    return True


def main() -> int:
    tests = [
        test_desktop_nav_shows_primary_items_only,
        test_mobile_primary_and_more_menus,
        test_settings_admin_area_links,
        test_search_links_to_all_bookings,
        test_driver_links_to_daily_checklist,
        test_all_bookings_renders_postgres_created_at,
        test_all_moved_routes_still_load,
        test_crew_page_has_schedule_and_management,
        test_existing_bookings_unchanged,
    ]
    passed = 0
    for test in tests:
        try:
            if test():
                passed += 1
                print("PASS:", test.__name__)
            else:
                print("FAIL:", test.__name__)
        except Exception as exc:
            print("FAIL:", test.__name__, "—", exc)
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
