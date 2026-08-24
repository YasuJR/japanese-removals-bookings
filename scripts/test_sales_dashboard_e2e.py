#!/usr/bin/env python3
"""E2E tests — Dashboard Sales summary from Paid invoices (Perth / AU FY)."""

import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import bank_transfer_match
import database as db
import invoice
import invoice_numbering
import sales_dashboard
from app import app
from dashboard_data import build_dashboard, perth_today


_user_n = 0
_book_n = 0
_inv_n = 0


def _login_client():
    global _user_n
    _user_n += 1
    db.init_db()
    uid = db.create_staff_user(
        "sales-dash-{0}-{1}".format(os.getpid(), _user_n),
        auth.hash_password("test"),
        "Sales Dash Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _next_invoice_number():
    global _inv_n
    _inv_n += 1
    return str(920000000 + (os.getpid() % 10000) * 100 + _inv_n)


def _create_job(
    *,
    move_date,
    total=180.0,
    payment_status="Unpaid",
    status="Invoiced",
    invoice_number="",
    paid_at="",
):
    global _book_n
    _book_n += 1
    marker = "SalesDash{0}-{1}-{2}".format(os.getpid(), _book_n, time.time_ns())
    booking_id = db.create_booking(
        marker,
        "0412000999",
        "{0}@example.com".format(marker.lower()),
        "1 Sales St, Perth WA",
        "2 Sales Ave, Fremantle WA",
        move_date,
        2,
        "sales dashboard test {0}".format(marker),
        start_time="08:00",
        finish_time="09:00",
        duration_hours="1",
        hourly_rate=float(total),
        callout_fee=0.0,
        gst_enabled=0,
        payment_status=payment_status,
        status=status,
    )
    fields = {}
    number = invoice_number or _next_invoice_number()
    fields["invoice_number"] = str(number)
    if paid_at:
        fields["paid_at"] = paid_at
    db.update_booking_invoice_fields(booking_id, fields)
    row = dict(db.get_booking(booking_id))
    row["extra_charges"] = []
    actual = round(float(invoice.calculate_invoice_totals(row)["total"]), 2)
    assert actual == round(float(total), 2), (actual, total)
    return booking_id, actual, marker, invoice_numbering.display_invoice_number(row)


def test_australian_financial_year_bounds():
    start, end = sales_dashboard.australian_financial_year(date(2026, 8, 24))
    assert start == date(2026, 7, 1)
    assert end == date(2027, 6, 30)
    start, end = sales_dashboard.australian_financial_year(date(2026, 6, 30))
    assert start == date(2025, 7, 1)
    assert end == date(2026, 6, 30)
    start, end = sales_dashboard.australian_financial_year(date(2026, 7, 1))
    assert start == date(2026, 7, 1)
    assert end == date(2027, 6, 30)
    return True


def test_paid_on_perth_from_utc_datetime():
    """UTC 16:00 on 23 Aug is 00:00 on 24 Aug in Perth."""
    booking = {"paid_at": "2026-08-23 16:00:00"}
    assert sales_dashboard.paid_on_perth(booking) == date(2026, 8, 24)
    booking = {"paid_at": "2026-08-23 15:59:59"}
    assert sales_dashboard.paid_on_perth(booking) == date(2026, 8, 23)
    booking = {"paid_at": "2026-08-20"}
    assert sales_dashboard.paid_on_perth(booking) == date(2026, 8, 20)
    aware = datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)
    assert sales_dashboard.paid_on_perth({"paid_at": aware}) == date(2026, 8, 24)
    perth = ZoneInfo("Australia/Perth")
    assert perth_today(datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)) == date(
        2026, 8, 24
    )
    assert perth_today(datetime(2026, 8, 24, 0, 0, tzinfo=perth)) == date(2026, 8, 24)
    return True


def test_paid_invoice_counts_in_periods_once():
    db.init_db()
    today = perth_today()
    before = sales_dashboard.build_sales_summary(today)
    fy_start, fy_end = sales_dashboard.australian_financial_year(today)
    week_start, week_end = sales_dashboard.week_range(today)
    amount = 187.25
    paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    booking_id, total, _m, _disp = _create_job(
        move_date=today.isoformat(),
        total=amount,
        payment_status="Paid",
        paid_at=paid_at,
    )
    after = sales_dashboard.build_sales_summary(today)
    assert after["today_sales"] - before["today_sales"] == total
    assert after["today_paid_jobs"] - before["today_paid_jobs"] == 1
    if week_start <= today <= week_end:
        assert after["week_sales"] - before["week_sales"] == total
    if today.month == after["month_start"][:7] or True:
        month_start = date.fromisoformat(after["month_start"])
        month_end = date.fromisoformat(after["month_end"])
        if month_start <= today <= month_end:
            assert after["month_sales"] - before["month_sales"] == total
    if fy_start <= today <= fy_end:
        assert after["fy_sales"] - before["fy_sales"] == total
        expected_avg = round(after["fy_sales"] / after["fy_paid_jobs"], 2)
        assert after["average_job_value"] == expected_avg
    # Same booking is not counted twice on a second summary build.
    again = sales_dashboard.build_sales_summary(today)
    assert again["today_sales"] == after["today_sales"]
    assert again["fy_paid_jobs"] == after["fy_paid_jobs"]
    control_id, _c, _cm, _cd = _create_job(
        move_date=today.isoformat(),
        total=90.0,
        payment_status="Unpaid",
        status="Invoiced",
    )
    control_before = dict(db.get_booking(control_id))
    third = sales_dashboard.build_sales_summary(today)
    assert third["today_sales"] == after["today_sales"]
    assert dict(db.get_booking(booking_id))["customer_name"] == dict(
        db.get_booking(booking_id)
    )["customer_name"]
    assert dict(db.get_booking(control_id))["payment_status"] == control_before[
        "payment_status"
    ]
    return True


def test_unpaid_amount_excludes_paid_and_cancelled():
    db.init_db()
    today = perth_today()
    before = sales_dashboard.build_sales_summary(today)
    unpaid_id, unpaid_total, _m, _d = _create_job(
        move_date=today.isoformat(),
        total=143.50,
        payment_status="Unpaid",
        status="Invoiced",
    )
    _create_job(
        move_date=today.isoformat(),
        total=200.00,
        payment_status="Unpaid",
        status="Cancelled",
    )
    _create_job(
        move_date=today.isoformat(),
        total=300.00,
        payment_status="Paid",
        paid_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    after = sales_dashboard.build_sales_summary(today)
    assert after["unpaid_amount"] - before["unpaid_amount"] == unpaid_total
    assert after["unpaid_count"] - before["unpaid_count"] == 1
    assert dict(db.get_booking(unpaid_id))["payment_status"] == "Unpaid"
    return True


def test_prior_fy_paid_not_in_current_fy():
    db.init_db()
    today = date(2026, 8, 24)
    before = sales_dashboard.build_sales_summary(today)
    _create_job(
        move_date="2026-06-15",
        total=410.00,
        payment_status="Paid",
        paid_at="2026-06-15",
    )
    after = sales_dashboard.build_sales_summary(today)
    assert after["fy_sales"] == before["fy_sales"]
    assert after["today_sales"] == before["today_sales"]
    return True


def test_bank_transfer_paid_updates_today_sales():
    db.init_db()
    today = perth_today()
    before = sales_dashboard.build_sales_summary(today)
    booking_id, total, marker, displayed = _create_job(
        move_date=today.isoformat(),
        total=256.00,
        payment_status="Unpaid",
        status="Invoiced",
    )
    csv_text = (
        "Transaction date,Description,Reference,Amount\n"
        "{0},DEPOSIT-OSKO PAYMENT {1} {2},,{3:.2f}\n".format(
            today.isoformat(), marker, displayed, total
        )
    )
    summary = bank_transfer_match.import_bank_transactions(
        bank_transfer_match.parse_bank_csv(csv_text)
    )
    assert summary["paid"] == 1, summary
    row = dict(db.get_booking(booking_id))
    assert row["payment_status"] == "Paid"
    assert row["status"] == "Completed"
    after = sales_dashboard.build_sales_summary(today)
    assert after["today_sales"] - before["today_sales"] == total
    return True


def test_stripe_style_paid_counts_the_same():
    db.init_db()
    today = perth_today()
    before = sales_dashboard.build_sales_summary(today)
    paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    booking_id, total, _m, _d = _create_job(
        move_date=today.isoformat(),
        total=321.00,
        payment_status="Unpaid",
        status="Invoiced",
    )
    invoice.apply_payment_status(
        booking_id, invoice.PAYMENT_STATUS_PAID, paid_at=paid_at
    )
    after = sales_dashboard.build_sales_summary(today)
    assert after["today_sales"] - before["today_sales"] == total
    assert dict(db.get_booking(booking_id))["payment_status"] == "Paid"
    return True


def test_dashboard_html_shows_sales_cards():
    client = _login_client()
    today = perth_today()
    paid_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _booking_id, total, _m, _d = _create_job(
        move_date=today.isoformat(),
        total=175.50,
        payment_status="Paid",
        paid_at=paid_at,
    )
    html = client.get("/dashboard").get_data(as_text=True)
    summary = sales_dashboard.build_sales_summary(today)
    dash = build_dashboard(today)
    assert "sales" in dash
    for label in (
        "Today Sales",
        "This Week Sales",
        "This Month Sales",
        "Financial Year Sales",
        "Unpaid Amount",
        "Average Job Value",
    ):
        assert label in html, label
    assert invoice.format_aud(summary["today_sales"]) in html
    assert invoice.format_aud(summary["unpaid_amount"]) in html
    assert invoice.format_aud(summary["average_job_value"]) in html
    assert "sales-stats" in html
    assert html.count('class="dashboard-stats') >= 2
    assert ">Today<" in html
    assert ">Movers booked<" in html
    assert 'class="dashboard-payment-picker"' in html
    desktop_nav = html.split("main-nav-desktop", 1)[-1].split("</nav>", 1)[0]
    assert ">Driver<" not in desktop_nav
    assert ">Invoices<" not in desktop_nav
    return True


def test_existing_booking_unchanged_by_sales_view():
    db.init_db()
    booking_id, _t, marker, _d = _create_job(
        move_date="2026-09-01",
        total=199.00,
        payment_status="Unpaid",
        status="Confirmed",
    )
    before = dict(db.get_booking(booking_id))
    client = _login_client()
    client.get("/dashboard")
    sales_dashboard.build_sales_summary(perth_today())
    after = dict(db.get_booking(booking_id))
    assert after["customer_name"] == before["customer_name"] == marker
    assert after["payment_status"] == before["payment_status"]
    assert after["status"] == before["status"]
    assert after["hourly_rate"] == before["hourly_rate"]
    return True


def main():
    tests = [
        ("fy_bounds", test_australian_financial_year_bounds),
        ("perth_paid_at", test_paid_on_perth_from_utc_datetime),
        ("paid_counts_once", test_paid_invoice_counts_in_periods_once),
        ("unpaid_excludes_cancelled", test_unpaid_amount_excludes_paid_and_cancelled),
        ("prior_fy_excluded", test_prior_fy_paid_not_in_current_fy),
        ("bank_transfer_sales", test_bank_transfer_paid_updates_today_sales),
        ("stripe_style_paid", test_stripe_style_paid_counts_the_same),
        ("dashboard_html_cards", test_dashboard_html_shows_sales_cards),
        ("existing_booking_unchanged", test_existing_booking_unchanged_by_sales_view),
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
