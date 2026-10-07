#!/usr/bin/env python3
"""Job note callout UI on Staff Portal and booking details."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")
os.environ["STAFF_PORTAL_PASSWORD"] = "jr-staff-portal-test-only"

import auth
import database as db
from app import app
from booking_helpers import notes_display
from dashboard_data import perth_today


def test_notes_display_preserves_newlines_and_links_phone():
    raw = "Pickup 1: 52 Kilkenny\nCall 0412 345 678 before arrival."
    html = str(notes_display(raw))
    assert "Pickup 1: 52 Kilkenny" in html
    assert "0412 345 678" in html
    assert 'href="tel:' in html
    assert "job-note-link" in html
    return True


def _ensure_default_crew():
    existing = {row["name"] for row in db.list_crew_members(active_only=False)}
    for name in ("Yasu", "Tom", "Ken"):
        if name not in existing:
            db.create_crew_member(name, role="Driver", active=1)


def test_staff_portal_today_shows_job_note_box():
    db.init_db()
    _ensure_default_crew()
    today = perth_today().isoformat()
    notes = "Pickup 1: 52 Kilkenny\nPickup 2: 3/132 Ardross\nDelivery: 43 Ogilvie St"
    db.create_booking(
        "Note UI Test",
        "0400111222",
        "note-ui@example.com",
        "1 A St, Perth WA",
        "2 B Rd, Perth WA",
        today,
        2,
        notes,
        start_time="08:00",
        finish_time="12:00",
        crew="Yasu",
        status="Confirmed",
    )
    client = app.test_client()
    login = client.post(
        "/staff/login",
        data={"password": "jr-staff-portal-test-only", "staff": "Yasu"},
        follow_redirects=False,
    )
    assert login.status_code == 302
    html = client.get("/staff?range=today&staff_id=all").get_data(as_text=True)
    assert "job-note-box" in html
    assert "JOB NOTE" in html
    assert "Pickup 1: 52 Kilkenny" in html
    assert "3/132 Ardross" in html
    assert 'class="job-note-box-body"' in html
    return True


def test_view_booking_shows_job_note_box():
    db.init_db()
    uid = db.create_staff_user(
        "note-ui-booking-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Note UI Booking",
    )
    bid = db.create_booking(
        "Booking Note Test",
        "0400333444",
        "b@example.com",
        "10 P St, Perth WA",
        "20 D Rd, Perth WA",
        "2026-12-15",
        2,
        "Please call customer before arrival.",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    html = client.get("/bookings/{0}".format(bid)).get_data(as_text=True)
    assert "job-note-box" in html
    assert "Please call customer before arrival." in html
    return True


def main():
    tests = [
        ("notes_display_filter", test_notes_display_preserves_newlines_and_links_phone),
        ("staff_portal_today_job_note", test_staff_portal_today_shows_job_note_box),
        ("view_booking_job_note", test_view_booking_shows_job_note_box),
    ]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("OK", name)
        except Exception as exc:
            failed.append((name, exc))
            print("FAIL", name, exc)
    if failed:
        sys.exit(1)
    print("All job note display tests passed.")


if __name__ == "__main__":
    main()
