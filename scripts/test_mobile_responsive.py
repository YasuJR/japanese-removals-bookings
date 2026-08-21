#!/usr/bin/env python3
"""Smoke tests for mobile responsive layout."""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")


def test_mobile_css_exists_and_covers_390px():
    css = (ROOT / "static" / "mobile.css").read_text()
    assert "@media (max-width: 767px)" in css
    assert ".touch-action-btn" in css
    assert "min-height: var(--touch)" in css
    assert ".mobile-quick-actions" in css
    assert ".calendar-page" in css
    return True


def test_base_template_links_mobile_css():
    html = (ROOT / "templates" / "base.html").read_text()
    assert "mobile.css" in html
    assert 'width=device-width' in html
    return True


def test_booking_list_has_mobile_cards_and_key_fields():
    html = (ROOT / "templates" / "_booking_list.html").read_text()
    assert "booking-cards" in html
    assert "booking-card-badges" in html
    assert "_contact_actions.html" in html
    assert "payment_status" in html
    assert "booking_job_status" in html
    assert "desktop-only" in html
    return True


def test_dashboard_uses_compact_table_only():
    html = (ROOT / "templates" / "dashboard.html").read_text()
    assert "dashboard-job-cards" not in html
    assert "dashboard-sheet" in html
    assert "dashboard-customer-invoice" in html
    assert 'class="table-scroll desktop-only"' not in html
    return True


def test_edit_booking_has_mobile_quick_actions():
    html = (ROOT / "templates" / "edit_booking.html").read_text()
    assert "mobile-quick-actions" in html
    assert "_contact_actions.html" in html
    assert "Pay link" in html
    return True


def test_new_booking_page_renders_mobile_friendly():
    import auth
    import database as db
    from app import app

    db.init_db()
    uid = db.create_staff_user(
        "mobile-test-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Mobile Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "mobile-test"

    new_booking_resp = client.get("/bookings/new")
    assert new_booking_resp.status_code == 200
    new_booking = new_booking_resp.get_data(as_text=True)
    assert "mobile.css" in new_booking
    assert "mobile-bottom-nav" in new_booking
    assert re.search(r'name="phone"[^>]*value=""', new_booking)
    assert re.search(r'name="email"[^>]*value=""', new_booking)
    assert re.search(r'name="duration_hours"[^>]*value=""', new_booking)
    assert re.search(
        r'<option value="Confirmed"[\s\S]*?\bselected\b',
        new_booking,
    )

    for path in ("/dashboard", "/bookings/upcoming", "/calendar"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        body = resp.get_data(as_text=True)
        assert "mobile.css" in body, path
        assert "mobile-bottom-nav" in body, path
    return True


def test_touch_target_css_at_least_44px():
    css = (ROOT / "static" / "mobile.css").read_text()
    match = re.search(r"--touch:\s*(\d+)px", css)
    assert match and int(match.group(1)) >= 44
    return True


def main():
    tests = [
        test_mobile_css_exists_and_covers_390px,
        test_base_template_links_mobile_css,
        test_booking_list_has_mobile_cards_and_key_fields,
        test_dashboard_uses_compact_table_only,
        test_edit_booking_has_mobile_quick_actions,
        test_new_booking_page_renders_mobile_friendly,
        test_touch_target_css_at_least_44px,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS:", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", fn.__name__, exc)
    print("\n{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
