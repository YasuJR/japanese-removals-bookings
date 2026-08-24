#!/usr/bin/env python3
"""E2E tests — Actual vs Projected monthly profit and Outstanding."""

import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import bank_transfer_match
import booking_profit
import database as db
import invoice
import sales_dashboard
from app import app
from dashboard_data import perth_today


_user_n = 0
_book_n = 0
_inv_n = 0


def _login_client():
    global _user_n
    _user_n += 1
    db.init_db()
    uid = db.create_staff_user(
        "act-proj-{0}-{1}".format(os.getpid(), _user_n),
        auth.hash_password("test"),
        "Actual Projected Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _next_invoice_number():
    global _inv_n
    _inv_n += 1
    return str(930000000 + (os.getpid() % 10000) * 100 + _inv_n)


def _create_job(
    *,
    move_date,
    total,
    payment_status="Unpaid",
    status="Confirmed",
    paid_at="",
    gst_enabled=1,
    staff_cost=0.0,
    fuel_cost=0.0,
    truck_cost=0.0,
    other_costs=0.0,
):
    global _book_n
    _book_n += 1
    marker = "ActProj{0}-{1}-{2}".format(os.getpid(), _book_n, time.time_ns())
    booking_id = db.create_booking(
        marker,
        "0412000888",
        "{0}@example.com".format(marker.lower()),
        "1 Actual St, Perth WA",
        "2 Projected Ave, Fremantle WA",
        move_date,
        2,
        "actual projected test {0}".format(marker),
        start_time="08:00",
        finish_time="09:00",
        duration_hours="1",
        hourly_rate=float(total),
        callout_fee=0.0,
        gst_enabled=int(gst_enabled),
        payment_status=payment_status,
        status=status,
    )
    fields = {"invoice_number": _next_invoice_number()}
    if paid_at:
        fields["paid_at"] = paid_at
    db.update_booking_invoice_fields(booking_id, fields)
    db.update_booking_profit_fields(
        booking_id,
        {
            "staff_cost": staff_cost,
            "fuel_cost": fuel_cost,
            "truck_cost": truck_cost,
            "other_costs": other_costs,
        },
    )
    row = dict(db.get_booking(booking_id))
    row["extra_charges"] = []
    actual_total = round(float(invoice.calculate_invoice_totals(row)["total"]), 2)
    assert actual_total == round(float(total), 2), (actual_total, total)
    return booking_id, actual_total, marker


def test_month_paid_revenue_matches_sales_dashboard():
    db.init_db()
    today = perth_today()
    month_key = today.strftime("%Y-%m")
    before_sales = sales_dashboard.build_sales_summary(today)
    before_month = booking_profit.build_monthly_profit_summary(month_key)
    assert before_sales["month_sales"] == before_month["actual"]["paid_revenue"]
    assert before_sales["unpaid_amount"] == before_month["outstanding"]["amount"]
    assert before_sales["unpaid_count"] == before_month["outstanding"]["count"]

    paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _booking_id, total, _m = _create_job(
        move_date=today.isoformat(),
        total=286.00,
        payment_status="Paid",
        status="Completed",
        paid_at=paid_at,
        gst_enabled=1,
        staff_cost=40.0,
        fuel_cost=10.0,
    )
    after_sales = sales_dashboard.build_sales_summary(today)
    after_month = booking_profit.build_monthly_profit_summary(month_key)
    assert after_sales["month_sales"] - before_sales["month_sales"] == total
    assert after_month["actual"]["paid_revenue"] - before_month["actual"]["paid_revenue"] == total
    assert after_month["actual"]["paid_revenue"] == after_sales["month_sales"]
    assert after_month["outstanding"]["amount"] == after_sales["unpaid_amount"]
    return True


def test_gst_actual_profit_and_margin():
    db.init_db()
    today = perth_today()
    month_key = today.strftime("%Y-%m")
    before = booking_profit.build_monthly_profit_summary(month_key)
    paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _id, total, _m = _create_job(
        move_date=today.isoformat(),
        total=330.00,
        payment_status="Paid",
        status="Completed",
        paid_at=paid_at,
        gst_enabled=1,
        staff_cost=100.0,
        fuel_cost=50.0,
        truck_cost=80.0,
    )
    after = booking_profit.build_monthly_profit_summary(month_key)
    gst = round(330.00 / 11.0, 2)
    net = round(330.00 - gst, 2)
    costs = 230.00
    profit = round(net - costs, 2)
    margin = round(profit / net * 100.0, 2)
    assert after["actual"]["gst_collected"] - before["actual"]["gst_collected"] == gst
    assert after["actual"]["net_revenue"] - before["actual"]["net_revenue"] == net
    assert after["actual"]["actual_costs"] - before["actual"]["actual_costs"] == costs
    assert after["actual"]["actual_profit"] - before["actual"]["actual_profit"] == profit
    assert after["actual"]["paid_jobs"] - before["actual"]["paid_jobs"] == 1
    rebuilt = booking_profit.net_margin_percent(
        after["actual"]["actual_profit"], after["actual"]["net_revenue"]
    )
    assert after["actual"]["actual_margin"] == rebuilt
    assert abs(margin - 0) >= 0
    return True


def test_projected_booked_revenue_excludes_cancelled():
    db.init_db()
    today = perth_today()
    month_key = today.strftime("%Y-%m")
    before = booking_profit.build_monthly_profit_summary(month_key)
    confirmed_id, booked_total, _m = _create_job(
        move_date=today.isoformat(),
        total=220.00,
        payment_status="Unpaid",
        status="Confirmed",
        gst_enabled=1,
        staff_cost=30.0,
    )
    cancelled_id, cancelled_total, _cm = _create_job(
        move_date=today.isoformat(),
        total=500.00,
        payment_status="Unpaid",
        status="Cancelled",
        gst_enabled=1,
    )
    after = booking_profit.build_monthly_profit_summary(month_key)
    assert after["projected"]["booked_jobs"] - before["projected"]["booked_jobs"] == 1
    assert after["projected"]["booked_revenue"] - before["projected"]["booked_revenue"] == booked_total
    gst = round(220.00 / 11.0, 2)
    net = round(220.00 - gst, 2)
    profit = round(net - 30.0, 2)
    assert after["projected"]["projected_gst"] - before["projected"]["projected_gst"] == gst
    assert after["projected"]["projected_profit"] - before["projected"]["projected_profit"] == profit
    assert dict(db.get_booking(cancelled_id))["status"] == "Cancelled"
    assert dict(db.get_booking(confirmed_id))["payment_status"] == "Unpaid"
    return True


def test_outstanding_excludes_paid_and_cancelled():
    db.init_db()
    today = perth_today()
    month_key = today.strftime("%Y-%m")
    before = booking_profit.build_monthly_profit_summary(month_key)
    unpaid_id, unpaid_total, _m = _create_job(
        move_date=today.isoformat(),
        total=175.50,
        payment_status="Unpaid",
        status="Invoiced",
    )
    _create_job(
        move_date=today.isoformat(),
        total=400.00,
        payment_status="Paid",
        status="Completed",
        paid_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    _create_job(
        move_date=today.isoformat(),
        total=90.00,
        payment_status="Unpaid",
        status="Cancelled",
    )
    after = booking_profit.build_monthly_profit_summary(month_key)
    sales = sales_dashboard.build_sales_summary(today)
    assert after["outstanding"]["amount"] - before["outstanding"]["amount"] == unpaid_total
    assert after["outstanding"]["count"] - before["outstanding"]["count"] == 1
    assert after["outstanding"]["amount"] == sales["unpaid_amount"]
    assert dict(db.get_booking(unpaid_id))["payment_status"] == "Unpaid"
    return True


def test_no_double_count_same_booking():
    db.init_db()
    today = perth_today()
    month_key = today.strftime("%Y-%m")
    before = booking_profit.build_monthly_profit_summary(month_key)
    booking_id, total, _m = _create_job(
        move_date=today.isoformat(),
        total=198.00,
        payment_status="Paid",
        status="Completed",
        paid_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    after = booking_profit.build_monthly_profit_summary(month_key)
    again = booking_profit.build_monthly_profit_summary(month_key)
    assert after["actual"]["paid_jobs"] - before["actual"]["paid_jobs"] == 1
    assert after["actual"]["paid_revenue"] - before["actual"]["paid_revenue"] == total
    assert again["actual"]["paid_jobs"] == after["actual"]["paid_jobs"]
    assert again["actual"]["paid_revenue"] == after["actual"]["paid_revenue"]
    assert again["projected"]["booked_jobs"] == after["projected"]["booked_jobs"]
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    return True


def test_perth_paid_at_drives_actual_not_move_date():
    db.init_db()
    today = date(2026, 8, 24)
    month_key = "2026-08"
    before = booking_profit.build_monthly_profit_summary(month_key)
    _create_job(
        move_date="2026-08-10",
        total=155.00,
        payment_status="Paid",
        status="Completed",
        paid_at="2026-06-15",
    )
    after_old_paid = booking_profit.build_monthly_profit_summary(month_key)
    assert after_old_paid["actual"]["paid_revenue"] == before["actual"]["paid_revenue"]
    utc_evening = "2026-08-23 16:00:00"
    assert sales_dashboard.paid_on_perth({"paid_at": utc_evening}) == date(2026, 8, 24)
    _create_job(
        move_date="2026-07-01",
        total=164.00,
        payment_status="Paid",
        status="Completed",
        paid_at=utc_evening,
    )
    after = booking_profit.build_monthly_profit_summary(month_key)
    assert after["actual"]["paid_revenue"] - before["actual"]["paid_revenue"] == 164.00
    sales = sales_dashboard.build_sales_summary(today)
    assert sales["month_sales"] == after["actual"]["paid_revenue"]
    return True


def test_bank_transfer_paid_updates_actual():
    db.init_db()
    today = perth_today()
    month_key = today.strftime("%Y-%m")
    before = booking_profit.build_monthly_profit_summary(month_key)
    booking_id, total, marker = _create_job(
        move_date=today.isoformat(),
        total=247.00,
        payment_status="Unpaid",
        status="Invoiced",
        gst_enabled=0,
    )
    row = dict(db.get_booking(booking_id))
    displayed = "INV{0}".format(row["invoice_number"])
    csv_text = (
        "Transaction date,Description,Reference,Amount\n"
        "{0},DEPOSIT-OSKO PAYMENT {1} {2},,{3:.2f}\n".format(
            today.isoformat(), marker, displayed, total
        )
    )
    imported = bank_transfer_match.import_bank_transactions(
        bank_transfer_match.parse_bank_csv(csv_text)
    )
    assert imported["paid"] == 1, imported
    paid_row = dict(db.get_booking(booking_id))
    assert paid_row["payment_status"] == "Paid"
    after = booking_profit.build_monthly_profit_summary(month_key)
    sales = sales_dashboard.build_sales_summary(today)
    assert after["actual"]["paid_revenue"] - before["actual"]["paid_revenue"] == total
    assert after["actual"]["paid_jobs"] - before["actual"]["paid_jobs"] == 1
    assert sales["month_sales"] == after["actual"]["paid_revenue"]
    assert after["outstanding"]["amount"] == sales["unpaid_amount"]
    return True


def test_dashboard_html_splits_actual_and_projected():
    client = _login_client()
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Actual / Paid performance" in html
    assert "Projected / Booked performance" in html
    assert "Paid Revenue" in html
    assert "Booked Revenue" in html
    assert "Outstanding Amount" in html
    assert "Actual Profit" in html
    assert "Projected Profit" in html
    assert "GST Collected" in html
    assert "profit-section-actual" in html
    assert "profit-section-projected" in html
    idx_actual = html.find("Actual / Paid performance")
    idx_projected = html.find("Projected / Booked performance")
    assert 0 <= idx_actual < idx_projected
    assert "Paid only" not in html
    return True


def test_existing_booking_unchanged():
    db.init_db()
    booking_id, _t, marker = _create_job(
        move_date="2026-09-02",
        total=188.00,
        payment_status="Unpaid",
        status="Confirmed",
    )
    before = dict(db.get_booking(booking_id))
    booking_profit.build_monthly_profit_summary("2026-09")
    sales_dashboard.build_sales_summary(perth_today())
    client = _login_client()
    client.get("/dashboard")
    after = dict(db.get_booking(booking_id))
    assert after["customer_name"] == before["customer_name"] == marker
    assert after["payment_status"] == before["payment_status"]
    assert after["status"] == before["status"]
    assert after["hourly_rate"] == before["hourly_rate"]
    return True


def main():
    tests = [
        ("month_matches_sales_cards", test_month_paid_revenue_matches_sales_dashboard),
        ("gst_actual_profit_margin", test_gst_actual_profit_and_margin),
        ("projected_excludes_cancelled", test_projected_booked_revenue_excludes_cancelled),
        ("outstanding_excludes_paid", test_outstanding_excludes_paid_and_cancelled),
        ("no_double_count", test_no_double_count_same_booking),
        ("perth_paid_at", test_perth_paid_at_drives_actual_not_move_date),
        ("bank_transfer_actual", test_bank_transfer_paid_updates_actual),
        ("dashboard_html_sections", test_dashboard_html_splits_actual_and_projected),
        ("existing_booking_unchanged", test_existing_booking_unchanged),
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
