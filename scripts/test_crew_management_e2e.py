#!/usr/bin/env python3
"""E2E tests for DB-backed crew management with history preservation."""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth
import database as db
from app import app
from crew import (
    active_crew_names,
    crew_from_storage,
    display_crew,
    merge_crew_for_edit,
)


class _Form(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value] if value else []


def _unique_username(prefix: str) -> str:
    return "{0}-{1}-{2}".format(prefix, os.getpid(), time.time_ns())


def _admin_client():
    db.init_db()
    username = _unique_username("crew-admin")
    uid = db.create_staff_user(
        username,
        auth.hash_password("test-password"),
        "Crew Admin",
        is_admin=1,
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = username
    return client


def _staff_client():
    db.init_db()
    username = _unique_username("crew-staff")
    uid = db.create_staff_user(
        username,
        auth.hash_password("test-password"),
        "Crew Staff",
        is_admin=0,
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = username
    return client


def test_crew_from_storage_preserves_unknown_names():
    names = crew_from_storage("Yasu,Former Member")
    assert names == ["Yasu", "Former Member"], names
    return True


def test_rename_preserves_booking_history():
    db.init_db()
    unique_name = "Rename Me {0}".format(time.time_ns())
    crew_id = db.create_crew_member(unique_name, "0400111222", "Driver", 1)
    booking_id = db.create_booking(
        "Rename History Customer",
        "0412000111",
        "rename@example.com",
        "1 Old St, Perth WA",
        "2 New St, Fremantle WA",
        "2099-01-15",
        2,
        "Rename test",
        crew=unique_name,
    )
    member = db.get_crew_member(crew_id)
    renamed = "Renamed Crew {0}".format(time.time_ns())
    db.update_crew_member(crew_id, renamed, member["phone"], member["role"], 1)
    row = db.get_booking(booking_id)
    assert display_crew(dict(row)) == unique_name, display_crew(dict(row))
    assert renamed in active_crew_names()
    return True


def test_deactivate_preserves_booking_on_edit():
    db.init_db()
    member = next(row for row in db.list_crew_members() if row["name"] == "Tom")
    booking_id = db.create_booking(
        "Deactivate History Customer",
        "0412000222",
        "deactivate@example.com",
        "3 Hold St, Perth WA",
        "4 Keep St, Fremantle WA",
        "2099-02-15",
        2,
        "Deactivate test",
        crew="Tom",
    )
    db.set_crew_member_active(member["id"], 0)
    assert "Tom" not in active_crew_names()

    merged = merge_crew_for_edit("Tom", _Form({"crew": []}))
    assert merged == ["Tom"], merged

    from crew import crew_storage_value

    ok = db.update_booking(
        booking_id=booking_id,
        customer_name="Deactivate History Customer",
        phone="0412000222",
        email="deactivate@example.com",
        pickup_address="3 Hold St, Perth WA",
        delivery_address="4 Keep St, Fremantle WA",
        move_date="2099-02-15",
        num_movers=2,
        notes="Deactivate test",
        start_time="09:00",
        finish_time="12:00",
        duration_hours="3",
        crew=crew_storage_value(merged),
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        payment_status="Unpaid",
        invoice_status="",
        status="Confirmed",
    )
    assert ok
    row = db.get_booking(booking_id)
    assert crew_from_storage(row["crew"]) == ["Tom"], row["crew"]
    return True


def test_new_crew_member_appears_in_booking_form():
    client = _admin_client()
    unique_name = "Mobile Crew {0}".format(os.getpid())
    resp = client.post(
        "/settings/crew",
        data={
            "action": "create",
            "name": unique_name,
            "phone": "0400000000",
            "role": "Driver",
            "return_to": "crew_schedule",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    assert unique_name in html
    assert "Crew schedule" in html
    assert "Add crew member" in html

    resp = client.get("/bookings/new")
    assert resp.status_code == 200, resp.status_code
    form_html = resp.get_data(as_text=True)
    assert unique_name in form_html
    return True


def test_crew_schedule_shows_management_for_admin():
    client = _admin_client()
    resp = client.get("/crew-schedule")
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    assert "Crew schedule" in html
    assert 'id="crew-management"' in html
    assert "Crew management" in html
    assert "Edit" in html
    assert "Add crew member" in html
    assert "Deactivate" in html or "Reactivate" in html
    return True


def test_crew_schedule_hides_management_for_staff():
    client = _staff_client()
    resp = client.get("/crew-schedule")
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    assert "Crew schedule" in html
    assert 'id="crew-management"' not in html
    assert "Add crew member" not in html
    return True


def test_admin_required_for_crew_management():
    staff = _staff_client()
    resp = staff.post(
        "/settings/crew",
        data={"action": "create", "name": "Blocked", "return_to": "crew_schedule"},
        follow_redirects=True,
    )
    assert resp.status_code == 200, resp.status_code
    assert "Admin access is required" in resp.get_data(as_text=True)

    admin = _admin_client()
    resp = admin.get("/settings/crew", follow_redirects=True)
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    assert "Crew management" in html
    assert "Crew schedule" in html
    return True


def test_delete_blocked_when_booking_history_exists():
    db.init_db()
    unique_name = "Delete Block {0}".format(os.getpid())
    crew_id = db.create_crew_member(unique_name, "", "Driver", 1)
    db.create_booking(
        "Delete Block Customer",
        "0412000333",
        "delete@example.com",
        "5 Block St, Perth WA",
        "6 Stop St, Fremantle WA",
        "2099-03-15",
        2,
        "Delete block test",
        crew=unique_name,
    )
    client = _admin_client()
    resp = client.post(
        "/settings/crew",
        data={
            "action": "delete",
            "crew_id": str(crew_id),
            "return_to": "crew_schedule",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200, resp.status_code
    assert "Deactivate instead" in resp.get_data(as_text=True)
    assert db.get_crew_member(crew_id) is not None
    return True


def test_migration_preserves_existing_bookings():
    db.init_db()
    booking_id = db.create_booking(
        "Migration Customer",
        "0412000444",
        "migration@example.com",
        "7 Safe St, Perth WA",
        "8 Keep St, Fremantle WA",
        "2099-04-15",
        2,
        "Migration test",
        crew="Ken",
    )
    before = dict(db.get_booking(booking_id))
    db.init_db()
    after = dict(db.get_booking(booking_id))
    assert after["crew"] == before["crew"], (before["crew"], after["crew"])
    assert after["customer_name"] == before["customer_name"]
    return True


def main() -> int:
    tests = [
        test_crew_from_storage_preserves_unknown_names,
        test_rename_preserves_booking_history,
        test_deactivate_preserves_booking_on_edit,
        test_new_crew_member_appears_in_booking_form,
        test_crew_schedule_shows_management_for_admin,
        test_crew_schedule_hides_management_for_staff,
        test_admin_required_for_crew_management,
        test_delete_blocked_when_booking_history_exists,
        test_migration_preserves_existing_bookings,
    ]
    passed = 0
    for test in tests:
        name = test.__name__
        try:
            if test():
                passed += 1
                print("PASS: {0}".format(name))
            else:
                print("FAIL: {0}".format(name))
        except Exception as exc:
            print("FAIL: {0} — {1}".format(name, exc))

    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
