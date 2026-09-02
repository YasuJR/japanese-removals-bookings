#!/usr/bin/env python3
"""Tests for Edit Booking UI cleanup and calendar page."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
from integrations import invoice_send
import calendar_data


def test_send_enabled_with_email():
    dest = invoice_send.resolve_send_destination(
        {"email": "customer@example.com", "phone": "0412345678"}
    )
    assert dest["can_send"], dest
    assert dest["method"] == "email"
    assert dest["destination"] == "customer@example.com"
    return True


def test_company_defaults_disabled():
    dest = invoice_send.resolve_send_destination(
        {
            "email": "info@japaneseremovals.com.au",
            "phone": "0481 089 573",
        }
    )
    assert not dest["can_send"], dest
    return True


def test_placeholder_email_uses_sms_when_phone_exists():
    dest = invoice_send.resolve_send_destination(
        {
            "email": "info@japaneseremovals.com.au",
            "phone": "0412987654",
        }
    )
    assert dest["can_send"], dest
    assert dest["method"] == "sms"
    return True


def test_send_enabled_with_phone_only():
    dest = invoice_send.resolve_send_destination({"email": "", "phone": "0412987654"})
    assert dest["can_send"], dest
    assert dest["method"] == "sms"
    return True


def test_send_disabled_without_contact():
    dest = invoice_send.resolve_send_destination({"email": "", "phone": ""})
    assert not dest["can_send"]
    assert "required" in dest["blocked_reason"].lower()
    return True


def test_profit_panel_removed_from_edit_template():
    text = (ROOT / "templates" / "edit_booking.html").read_text()
    assert "_profit_panel.html" not in text
    assert "Profit calculation" not in text
    return True


def test_driver_on_route_removed_from_edit_template():
    text = (ROOT / "templates" / "edit_booking.html").read_text()
    assert "Driver on route" not in text
    assert "on-route-panel" not in text
    assert 'name="driver_name"' not in text
    assert 'name="driver_origin"' not in text
    assert 'name="manual_eta_minutes"' not in text
    assert 'value="start_on_route"' not in text
    assert "phase10-automation-status" not in text
    return True


def test_calendar_loads_bookings():
    db.init_db()
    booking_id = db.create_booking(
        "Calendar Test",
        "0412000999",
        "cal@example.com",
        "1 Cal St, Perth WA",
        "2 Cal Ave, Fremantle WA",
        "2026-08-15",
        2,
        "Calendar test booking",
        start_time="09:00",
        finish_time="12:00",
        duration_hours="3",
        status="Confirmed",
    )
    ctx = calendar_data.build_calendar_context(year=2026, month=8, view="month")
    ids = {e["id"] for e in ctx["events"]}
    assert booking_id in ids, "Booking should appear on August 2026 calendar"
    return True


def test_calendar_navigation_fields():
    ctx = calendar_data.build_calendar_context(year=2026, month=8, day=15, view="month")
    assert ctx["prev_year"] and ctx["next_year"]
    assert "month_grid" in ctx and ctx["month_grid"]
    return True


def main():
    tests = [
        test_send_enabled_with_email,
        test_company_defaults_disabled,
        test_placeholder_email_uses_sms_when_phone_exists,
        test_send_enabled_with_phone_only,
        test_send_disabled_without_contact,
        test_profit_panel_removed_from_edit_template,
        test_driver_on_route_removed_from_edit_template,
        test_calendar_loads_bookings,
        test_calendar_navigation_fields,
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
