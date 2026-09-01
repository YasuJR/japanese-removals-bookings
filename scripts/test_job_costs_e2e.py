#!/usr/bin/env python3
"""E2E tests — per-booking Job Costs input and Dashboard cost totals."""

import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import booking_profit
import database as db
import invoice
from app import app
from dashboard_data import perth_today


_user_n = 0
_book_n = 0


def _login_client():
    global _user_n
    _user_n += 1
    db.init_db()
    uid = db.create_staff_user(
        "job-cost-{0}-{1}".format(os.getpid(), _user_n),
        auth.hash_password("test"),
        "Job Cost Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _metrics(booking_id):
    row = dict(db.get_booking(booking_id))
    row["extra_charges"] = db.list_extra_charges(booking_id)
    return booking_profit.calculate_booking_profit(row)


def _create_booking_a(**overrides):
    global _book_n
    _book_n += 1
    marker = "JobCostA-{0}-{1}".format(os.getpid(), _book_n)
    today = perth_today().isoformat()
    fields = dict(
        customer_name=marker,
        phone="0412000111",
        email="{0}@example.com".format(marker.lower()),
        pickup_address="1 Cost St, Perth WA",
        delivery_address="2 Profit Ave, Fremantle WA",
        move_date=today,
        num_movers=2,
        notes="job cost booking a {0}".format(marker),
        start_time="08:00",
        finish_time="09:00",
        duration_hours="1",
        hourly_rate=1100.0,
        callout_fee=0.0,
        gst_enabled=1,
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
        status="Confirmed",
    )
    fields.update(overrides)
    booking_id = db.create_booking(**fields)
    return booking_id, marker


def _edit_form(booking_id, **cost_overrides):
    row = dict(db.get_booking(booking_id))
    form = {
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "email": row["email"],
        "pickup_address": row["pickup_address"],
        "delivery_address": row["delivery_address"],
        "move_date": row["move_date"],
        "num_movers": str(row["num_movers"] or 2),
        "notes": row["notes"] or "",
        "start_time": row["start_time"] or "08:00",
        "finish_time": row["finish_time"] or "09:00",
        "duration_hours": row["duration_hours"] or "1",
        "hourly_rate": str(row["hourly_rate"] if row["hourly_rate"] is not None else 1100),
        "callout_fee": str(row["callout_fee"] if row["callout_fee"] is not None else 0),
        "gst_enabled": "on",
        "payment_status": row["payment_status"] or "Unpaid",
        "invoice_status": row["invoice_status"] or "",
        "status": row["status"] or "Confirmed",
        "action": "save",
        "double_booking_override_confirm": "on",
        "staff_cost": "300",
        "fuel_cost": "50",
        "truck_cost": "100",
        "parking_cost": "20",
        "other_costs": "30",
    }
    form.update(cost_overrides)
    return form


def test_booking_a_formula():
    db.init_db()
    booking_id, _marker = _create_booking_a()
    db.update_booking_profit_fields(
        booking_id,
        {
            "staff_cost": 300.0,
            "fuel_cost": 50.0,
            "truck_cost": 100.0,
            "parking_cost": 20.0,
            "other_costs": 30.0,
        },
    )
    invoice.set_payment_status(booking_id, True)
    booking_profit.recalculate_and_save(booking_id)
    metrics = _metrics(booking_id)
    assert metrics["revenue"] == 1100.0
    assert metrics["gst_amount"] == 100.0
    assert metrics["net_revenue"] == 1000.0
    assert metrics["staff_cost"] == 300.0
    assert metrics["fuel_cost"] == 50.0
    assert metrics["truck_cost"] == 100.0
    assert metrics["parking_cost"] == 20.0
    assert metrics["other_costs"] == 30.0
    assert metrics["total_job_cost"] == 500.0
    assert metrics["total_costs"] == 500.0
    assert metrics["estimated_profit"] == 500.0
    assert metrics["profit_margin_percent"] == 50.0
    stored = dict(db.get_booking(booking_id))
    assert round(float(stored["estimated_profit"]), 2) == 500.0
    assert round(float(stored["net_revenue"]), 2) == 1000.0
    return True


def test_empty_costs_are_zero_and_missing_parking_is_zero():
    db.init_db()
    booking_id, marker = _create_booking_a()
    metrics = _metrics(booking_id)
    assert metrics["staff_cost"] == 0.0
    assert metrics["fuel_cost"] == 0.0
    assert metrics["truck_cost"] == 0.0
    assert metrics["parking_cost"] == 0.0
    assert metrics["other_costs"] == 0.0
    assert metrics["total_job_cost"] == 0.0
    payload = dict(db.get_booking(booking_id))
    payload.pop("parking_cost", None)
    payload["extra_charges"] = []
    missing = booking_profit.calculate_booking_profit(payload)
    assert missing["parking_cost"] == 0.0
    assert missing["total_job_cost"] == 0.0
    after = dict(db.get_booking(booking_id))
    assert after["customer_name"] == marker
    assert after["hourly_rate"] == 1100.0
    assert after["payment_status"] == invoice.PAYMENT_STATUS_UNPAID
    return True


def test_negative_costs_rejected():
    errors = booking_profit.job_cost_form_errors({"staff_cost": "-1", "fuel_cost": "0"})
    assert errors
    assert any("negative" in msg.lower() for msg in errors)
    parsed = booking_profit.parse_money_amount("-20.50")
    assert parsed[0] is None
    empty = booking_profit.parse_money_amount("")
    assert empty == (0.0, None)
    two_dp = booking_profit.parse_money_amount("45.999")
    assert two_dp[0] == 46.0
    return True


def test_edit_and_view_show_job_costs_and_save():
    client = _login_client()
    booking_id, marker = _create_booking_a()
    db.update_booking_profit_fields(
        booking_id,
        {
            "staff_cost": 300.0,
            "fuel_cost": 50.0,
            "truck_cost": 100.0,
            "parking_cost": 20.0,
            "other_costs": 30.0,
        },
    )
    before = dict(db.get_booking(booking_id))
    edit_html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "Job Costs" not in edit_html
    assert 'name="staff_cost"' not in edit_html
    assert 'name="fuel_cost"' not in edit_html
    assert 'name="truck_cost"' not in edit_html
    assert 'name="parking_cost"' not in edit_html
    assert 'name="other_costs"' not in edit_html
    assert "Total Job Cost" not in edit_html
    assert "_profit_panel.html" not in (ROOT / "templates" / "edit_booking.html").read_text()
    assert "Profit calculation" not in edit_html
    save_form = _edit_form(booking_id)
    for key in (
        "staff_cost",
        "fuel_cost",
        "truck_cost",
        "parking_cost",
        "other_costs",
        "staff_cost_manual",
    ):
        save_form.pop(key, None)
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=save_form,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    row = dict(db.get_booking(booking_id))
    assert row["customer_name"] == before["customer_name"] == marker
    assert row["hourly_rate"] == before["hourly_rate"]
    assert round(float(row["staff_cost"] or 0), 2) == 300.0
    assert round(float(row["fuel_cost"] or 0), 2) == 50.0
    assert round(float(row["truck_cost"] or 0), 2) == 100.0
    assert round(float(row["parking_cost"] or 0), 2) == 20.0
    assert round(float(row["other_costs"] or 0), 2) == 30.0
    metrics = _metrics(booking_id)
    assert metrics["total_job_cost"] == 500.0
    assert metrics["net_revenue"] == 1000.0
    assert metrics["estimated_profit"] == 500.0
    assert metrics["profit_margin_percent"] == 50.0

    view_html = client.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    assert "Job Costs" in view_html
    assert "Staff Cost" in view_html
    assert "$300.00" in view_html
    assert "$50.00" in view_html
    assert "$100.00" in view_html
    assert "$20.00" in view_html
    assert "$30.00" in view_html
    assert "$500.00" in view_html
    assert "Revenue" in view_html
    assert "$1,100.00" in view_html
    assert "Profit" in view_html
    assert "50.0%" in view_html
    assert '<form method="post"' not in view_html

    new_html = client.get("/bookings/new").get_data(as_text=True)
    assert "Job Costs" in new_html
    assert 'name="staff_cost"' in new_html

    negative = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_edit_form(booking_id, staff_cost="-10"),
        follow_redirects=True,
    )
    assert negative.status_code == 200
    assert b"cannot be negative" in negative.data
    after_negative = dict(db.get_booking(booking_id))
    assert round(float(after_negative["staff_cost"] or 0), 2) == 300.0
    return True


def test_dashboard_actual_and_projected_use_job_cost():
    db.init_db()
    today = perth_today()
    month_key = today.strftime("%Y-%m")
    before = booking_profit.build_monthly_profit_summary(month_key)
    booking_id, _marker = _create_booking_a(move_date=today.isoformat())
    db.update_booking_profit_fields(
        booking_id,
        {
            "staff_cost": 300.0,
            "fuel_cost": 50.0,
            "truck_cost": 100.0,
            "parking_cost": 20.0,
            "other_costs": 30.0,
        },
    )
    projected = booking_profit.build_monthly_profit_summary(month_key)
    assert projected["projected"]["projected_costs"] - before["projected"]["projected_costs"] == 500.0
    assert projected["projected"]["projected_net_revenue"] - before["projected"]["projected_net_revenue"] == 1000.0
    assert projected["projected"]["projected_profit"] - before["projected"]["projected_profit"] == 500.0

    paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    invoice.set_payment_status(booking_id, True)
    db.update_booking_invoice_fields(booking_id, {"paid_at": paid_at})
    db.update_booking_status(booking_id, "Completed")
    after = booking_profit.build_monthly_profit_summary(month_key)
    assert after["actual"]["actual_costs"] - before["actual"]["actual_costs"] == 500.0
    assert after["actual"]["net_revenue"] - before["actual"]["net_revenue"] == 1000.0
    assert after["actual"]["actual_profit"] - before["actual"]["actual_profit"] == 500.0
    rebuilt = booking_profit.net_margin_percent(
        after["actual"]["actual_profit"], after["actual"]["net_revenue"]
    )
    assert after["actual"]["actual_margin"] == rebuilt
    return True


def test_search_and_calendar_templates_unchanged():
    search = (ROOT / "templates" / "search.html").read_text()
    calendar = (ROOT / "templates" / "calendar.html").read_text()
    booking_list = (ROOT / "templates" / "_booking_list.html").read_text()
    assert "Job Costs" not in search
    assert "staff_cost" not in search
    assert "Job Costs" not in calendar
    assert "staff_cost" not in calendar
    assert "Total Job Cost" not in booking_list
    return True


def _post_new_job(
    client,
    *,
    start_time,
    finish_time,
    duration_hours,
    marker=None,
    **cost_fields,
):
    marker = marker or "JobCostDef-{0}-{1}".format(os.getpid(), time.time_ns())
    data = {
        "customer_name": marker,
        "phone": "0412000222",
        "email": "{0}@example.com".format(marker.lower()),
        "pickup_address": "10 New Cost St, Perth WA",
        "delivery_address": "20 New Profit Ave, Fremantle WA",
        "move_date": perth_today().isoformat(),
        "num_movers": "2",
        "notes": "job cost defaults {0}".format(marker),
        "start_time": start_time,
        "finish_time": finish_time,
        "duration_hours": duration_hours,
        "hourly_rate": "220",
        "callout_fee": "0",
        "gst_enabled": "on",
        "status": "Confirmed",
        "action": "save",
        "double_booking_override_confirm": "on",
        "staff_cost": "",
        "fuel_cost": "",
        "truck_cost": "",
        "parking_cost": "",
        "other_costs": "",
        "staff_cost_manual": "0",
    }
    data.update(cost_fields)
    resp = client.post("/bookings/new", data=data, follow_redirects=False)
    assert resp.status_code in (302, 303), resp.status_code
    matches = [
        dict(row)
        for row in db.list_all()
        if dict(row).get("customer_name") == marker
    ]
    assert matches, marker
    return matches[0], marker


def test_staff_cost_rate_examples():
    assert booking_profit.staff_cost_rate_per_hour(2) == 72.0
    assert booking_profit.staff_cost_rate_per_hour(3) == 108.0
    assert booking_profit.default_staff_cost(2) == 144.0
    assert booking_profit.default_staff_cost(3) == 216.0
    assert booking_profit.default_staff_cost(3.5) == 252.0
    assert booking_profit.default_staff_cost(5) == 360.0
    assert booking_profit.default_staff_cost(3, 2) == 216.0
    assert booking_profit.default_staff_cost(3, 3) == 324.0
    assert booking_profit.default_staff_cost(4, 2) == 288.0
    assert booking_profit.default_staff_cost(4, 3) == 432.0
    assert booking_profit.default_staff_cost(3.5, 2) == 252.0
    assert booking_profit.default_staff_cost(3.5, 3) == 378.0
    return True


def test_new_booking_duration_defaults():
    client = _login_client()
    new_html = client.get("/bookings/new").get_data(as_text=True)
    assert 'name="fuel_cost"' in new_html
    assert 'value="30.00"' in new_html

    cases = [
        ("08:00", "10:00", "2", "2", 144.0, 30.0, 174.0),
        ("08:00", "11:00", "3", "2", 216.0, 30.0, 246.0),
        ("08:00", "11:00", "3", "3", 324.0, 30.0, 354.0),
        ("08:00", "11:30", "3.5", "2", 252.0, 30.0, 282.0),
        ("08:00", "11:30", "3.5", "3", 378.0, 30.0, 408.0),
        ("08:00", "12:00", "4", "2", 288.0, 30.0, 318.0),
        ("08:00", "12:00", "4", "3", 432.0, 30.0, 462.0),
        ("08:00", "13:00", "5", "2", 360.0, 30.0, 390.0),
    ]
    for start, finish, duration, movers, staff, fuel, total in cases:
        row, _marker = _post_new_job(
            client,
            start_time=start,
            finish_time=finish,
            duration_hours=duration,
            num_movers=movers,
        )
        assert round(float(row.get("staff_cost") or 0), 2) == staff, (movers, duration, row)
        assert round(float(row.get("fuel_cost") or 0), 2) == fuel, duration
        assert round(float(row.get("truck_cost") or 0), 2) == 0.0
        assert round(float(row.get("parking_cost") or 0), 2) == 0.0
        assert round(float(row.get("other_costs") or 0), 2) == 0.0
        metrics = _metrics(row["id"])
        assert metrics["total_job_cost"] == total
        assert metrics["staff_cost"] == staff
        assert metrics["fuel_cost"] == fuel
    return True


def test_manual_staff_cost_is_kept():
    client = _login_client()
    row, marker = _post_new_job(
        client,
        start_time="08:00",
        finish_time="10:00",
        duration_hours="2",
        staff_cost="199.50",
        fuel_cost="30",
        staff_cost_manual="1",
    )
    assert round(float(row.get("staff_cost") or 0), 2) == 199.5
    assert round(float(row.get("fuel_cost") or 0), 2) == 30.0
    form = _edit_form(
        row["id"],
        staff_cost="199.50",
        fuel_cost="30",
        truck_cost="0",
        parking_cost="0",
        other_costs="0",
        staff_cost_manual="1",
        finish_time="11:00",
        duration_hours="3",
    )
    resp = client.post(
        "/bookings/{0}/edit".format(row["id"]),
        data=form,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    after = dict(db.get_booking(row["id"]))
    assert after["customer_name"] == marker
    assert round(float(after.get("staff_cost") or 0), 2) == 199.5
    assert round(float(after.get("fuel_cost") or 0), 2) == 30.0
    return True


def test_existing_saved_costs_not_overwritten():
    client = _login_client()
    booking_id, marker = _create_booking_a(
        start_time="08:00",
        finish_time="10:00",
        duration_hours="2",
    )
    db.update_booking_profit_fields(
        booking_id,
        {
            "staff_cost": 300.0,
            "fuel_cost": 50.0,
            "truck_cost": 10.0,
            "parking_cost": 5.0,
            "other_costs": 1.0,
        },
    )
    before = dict(db.get_booking(booking_id))
    form = _edit_form(
        booking_id,
        staff_cost="300",
        fuel_cost="50",
        truck_cost="10",
        parking_cost="5",
        other_costs="1",
        staff_cost_manual="1",
    )
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=form,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    after = dict(db.get_booking(booking_id))
    assert after["customer_name"] == before["customer_name"] == marker
    assert after["hourly_rate"] == before["hourly_rate"]
    assert after["payment_status"] == before["payment_status"]
    assert round(float(after["staff_cost"]), 2) == 300.0
    assert round(float(after["fuel_cost"]), 2) == 50.0
    assert round(float(after["truck_cost"]), 2) == 10.0
    assert round(float(after["parking_cost"]), 2) == 5.0
    assert round(float(after["other_costs"]), 2) == 1.0
    return True


def test_duration_change_recalculates_auto_staff():
    client = _login_client()
    booking_id, _marker = _create_booking_a(
        start_time="08:00",
        finish_time="10:00",
        duration_hours="2",
    )
    db.update_booking_profit_fields(
        booking_id,
        {"staff_cost": 144.0, "fuel_cost": 30.0},
    )
    form = _edit_form(
        booking_id,
        staff_cost="144",
        fuel_cost="30",
        truck_cost="0",
        parking_cost="0",
        other_costs="0",
        staff_cost_manual="0",
        finish_time="11:00",
        duration_hours="2",
    )
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=form,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    after = dict(db.get_booking(booking_id))
    assert str(after.get("finish_time") or "").startswith("11:00")
    assert round(float(after.get("staff_cost") or 0), 2) == 216.0
    assert round(float(after.get("fuel_cost") or 0), 2) == 30.0
    return True


def test_movers_change_recalculates_auto_staff():
    client = _login_client()
    row, marker = _post_new_job(
        client,
        start_time="08:00",
        finish_time="11:00",
        duration_hours="3",
        num_movers="2",
    )
    assert round(float(row.get("staff_cost") or 0), 2) == 216.0
    booking_id = int(row["id"])
    to_three = _edit_form(
        booking_id,
        staff_cost="216",
        fuel_cost="30",
        truck_cost="0",
        parking_cost="0",
        other_costs="0",
        staff_cost_manual="0",
        num_movers="3",
    )
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=to_three,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    after_three = dict(db.get_booking(booking_id))
    assert int(after_three["num_movers"]) == 3
    assert round(float(after_three.get("staff_cost") or 0), 2) == 324.0
    assert round(float(after_three.get("fuel_cost") or 0), 2) == 30.0

    to_two = _edit_form(
        booking_id,
        staff_cost="324",
        fuel_cost="30",
        truck_cost="0",
        parking_cost="0",
        other_costs="0",
        staff_cost_manual="0",
        num_movers="2",
    )
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=to_two,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    after_two = dict(db.get_booking(booking_id))
    assert after_two["customer_name"] == marker
    assert int(after_two["num_movers"]) == 2
    assert round(float(after_two.get("staff_cost") or 0), 2) == 216.0
    return True


def test_manual_staff_not_overwritten_when_movers_change():
    client = _login_client()
    row, _marker = _post_new_job(
        client,
        start_time="08:00",
        finish_time="11:00",
        duration_hours="3",
        num_movers="2",
        staff_cost="199.50",
        fuel_cost="30",
        staff_cost_manual="1",
    )
    form = _edit_form(
        row["id"],
        staff_cost="199.50",
        fuel_cost="30",
        truck_cost="0",
        parking_cost="0",
        other_costs="0",
        staff_cost_manual="1",
        num_movers="3",
    )
    resp = client.post(
        "/bookings/{0}/edit".format(row["id"]),
        data=form,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    after = dict(db.get_booking(row["id"]))
    assert int(after["num_movers"]) == 3
    assert round(float(after.get("staff_cost") or 0), 2) == 199.5
    return True


def test_dashboard_uses_default_job_costs():
    client = _login_client()
    today = perth_today()
    month_key = today.strftime("%Y-%m")
    before = booking_profit.build_monthly_profit_summary(month_key)
    row, _marker = _post_new_job(
        client,
        start_time="08:00",
        finish_time="10:00",
        duration_hours="2",
    )
    booking_id = int(row["id"])
    projected = booking_profit.build_monthly_profit_summary(month_key)
    assert projected["projected"]["projected_costs"] - before["projected"]["projected_costs"] == 174.0
    paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    invoice.set_payment_status(booking_id, True)
    db.update_booking_invoice_fields(booking_id, {"paid_at": paid_at})
    db.update_booking_status(booking_id, "Completed")
    after = booking_profit.build_monthly_profit_summary(month_key)
    assert after["actual"]["actual_costs"] - before["actual"]["actual_costs"] == 174.0

    before_three = booking_profit.build_monthly_profit_summary(month_key)
    row3, _m3 = _post_new_job(
        client,
        start_time="08:00",
        finish_time="11:00",
        duration_hours="3",
        num_movers="3",
    )
    three = booking_profit.build_monthly_profit_summary(month_key)
    assert three["projected"]["projected_costs"] - before_three["projected"]["projected_costs"] == 354.0
    paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    invoice.set_payment_status(int(row3["id"]), True)
    db.update_booking_invoice_fields(int(row3["id"]), {"paid_at": paid_at})
    db.update_booking_status(int(row3["id"]), "Completed")
    paid_three = booking_profit.build_monthly_profit_summary(month_key)
    assert paid_three["actual"]["actual_costs"] - before_three["actual"]["actual_costs"] == 354.0
    return True


def test_new_booking_optional_job_costs():
    client = _login_client()
    marker = "JobCostNew-{0}-{1}".format(os.getpid(), time.time_ns())
    move_date = perth_today().isoformat()
    resp = client.post(
        "/bookings/new",
        data={
            "customer_name": marker,
            "phone": "0412000222",
            "email": "{0}@example.com".format(marker.lower()),
            "pickup_address": "10 New Cost St, Perth WA",
            "delivery_address": "20 New Profit Ave, Fremantle WA",
            "move_date": move_date,
            "num_movers": "2",
            "notes": "optional job costs",
            "start_time": "08:00",
            "finish_time": "09:00",
            "duration_hours": "1",
            "hourly_rate": "220",
            "callout_fee": "0",
            "gst_enabled": "on",
            "status": "Confirmed",
            "action": "save",
            "double_booking_override_confirm": "on",
            "staff_cost": "12.5",
            "fuel_cost": "",
            "truck_cost": "7",
            "parking_cost": "",
            "other_costs": "0",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.status_code
    matches = [
        dict(row)
        for row in db.list_all()
        if dict(row).get("customer_name") == marker
    ]
    assert matches
    row = matches[0]
    assert round(float(row.get("staff_cost") or 0), 2) == 12.5
    assert round(float(row.get("fuel_cost") or 0), 2) == 30.0
    assert round(float(row.get("truck_cost") or 0), 2) == 7.0
    assert round(float(row.get("parking_cost") or 0), 2) == 0.0
    assert round(float(row.get("other_costs") or 0), 2) == 0.0
    metrics = _metrics(row["id"])
    assert metrics["total_job_cost"] == 49.5
    return True


def main():
    tests = [
        ("booking_a_formula", test_booking_a_formula),
        ("empty_and_missing_parking", test_empty_costs_are_zero_and_missing_parking_is_zero),
        ("negative_rejected", test_negative_costs_rejected),
        ("edit_view_save", test_edit_and_view_show_job_costs_and_save),
        ("dashboard_actual_projected", test_dashboard_actual_and_projected_use_job_cost),
        ("search_calendar_unchanged", test_search_and_calendar_templates_unchanged),
        ("staff_rate_examples", test_staff_cost_rate_examples),
        ("new_booking_duration_defaults", test_new_booking_duration_defaults),
        ("manual_staff_kept", test_manual_staff_cost_is_kept),
        ("existing_costs_not_overwritten", test_existing_saved_costs_not_overwritten),
        ("duration_change_auto_staff", test_duration_change_recalculates_auto_staff),
        ("movers_change_auto_staff", test_movers_change_recalculates_auto_staff),
        ("manual_staff_movers_kept", test_manual_staff_not_overwritten_when_movers_change),
        ("dashboard_default_job_costs", test_dashboard_uses_default_job_costs),
        ("new_booking_optional_costs", test_new_booking_optional_job_costs),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS:", name)
        except Exception as exc:
            failed += 1
            print("FAIL:", name, exc)
    print("\n{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
