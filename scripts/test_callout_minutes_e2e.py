#!/usr/bin/env python3
"""Call-out minutes, fee auto-calc, staff paid hours, and invoice line items."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SECRET_KEY", "test-callout-minutes")

import callout_pricing
import database as db
import extra_charges
import invoice
import staff_job_times
from validators import parse_booking_form


class _Form(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _booking(**fields):
    base = {
        "hourly_rate": 180.0,
        "callout_fee": 0.0,
        "callout_minutes": 0,
        "duration_hours": "6",
        "gst_enabled": 0,
        "extra_charges": [],
    }
    base.update(fields)
    return base


def test_fee_amount_presets():
    assert callout_pricing.fee_amount(180, 30) == 90.0
    assert callout_pricing.fee_amount(180, 45) == 135.0
    assert callout_pricing.fee_amount(180, 60) == 180.0
    assert callout_pricing.fee_amount(180, 90) == 270.0
    assert callout_pricing.fee_amount(235, 30) == 117.5
    assert callout_pricing.fee_amount(235, 45) == 176.25
    return True


def test_staff_callout_hours_from_minutes():
    for minutes, hours in ((30, 0.5), (45, 0.75), (60, 1.0)):
        booking = _booking(callout_minutes=minutes, callout_fee=callout_pricing.fee_amount(180, minutes))
        assert staff_job_times.callout_hours(booking) == hours
    return True


def test_paid_hours_actual_break_callout():
    booking = _booking(
        callout_minutes=45,
        callout_fee=135.0,
        actual_start_time="08:00",
        actual_finish_time="14:00",
        status="Completed",
    )
    booking["extra_charges"] = [
        {"description": "Unpaid Break", "quantity": 1, "unit_price": 0, "sort_order": 0}
    ]
    # break via extra_charges helper — use realistic break row
    booking = _booking(
        callout_minutes=45,
        callout_fee=135.0,
        actual_start_time="08:00",
        actual_finish_time="14:00",
        status="Invoiced",
    )
    db.init_db()
    bid = db.create_booking(
        "Callout Paid Test",
        "0400000000",
        "t@example.com",
        "1 A St",
        "2 B St",
        "2099-01-01",
        2,
        "",
        hourly_rate=180.0,
        callout_fee=135.0,
        callout_minutes=45,
    )
    db.save_booking_actual_times(bid, "08:00", "14:00", 360)
    db.replace_extra_charges(
        bid,
        [
            {
                "description": extra_charges.break_deduction_description(30),
                "quantity": 1,
                "unit_price": extra_charges.break_deduction_unit_price(180.0, 30),
            }
        ],
    )
    row = dict(db.get_booking(bid))
    row["extra_charges"] = db.list_extra_charges(bid)
    assert staff_job_times.callout_hours(row) == 0.75
    paid = staff_job_times.paid_hours(row)
    assert paid == 6.25
    return True


def test_validator_auto_fee_and_minutes():
    form = _Form(
        {
            "customer_name": "A",
            "pickup_address": "P",
            "delivery_address": "D",
            "move_date": "2099-02-01",
            "num_movers": "2",
            "hourly_rate": "180",
            "callout_minutes": "45",
            "callout_fee": "999",
            "crew": ["Yasu"],
        }
    )
    data, errors = parse_booking_form(form)
    assert not errors
    assert data["callout_minutes"] == 45
    assert data["callout_fee"] == 135.0
    return True


def test_legacy_infer_and_backfill():
    assert callout_pricing.infer_minutes_from_legacy(180, 90) == 30
    assert callout_pricing.infer_minutes_from_legacy(180, 135) == 45
    assert callout_pricing.infer_minutes_from_legacy(180, 100) is None
    booking = {"hourly_rate": 180.0, "callout_fee": 90.0}
    assert callout_pricing.minutes_for_booking(booking) == 30
    assert staff_job_times.callout_hours(booking) == 0.5
    return True


def test_invoice_callout_line_qty():
    booking = _booking(callout_minutes=45, callout_fee=135.0)
    totals = invoice.calculate_invoice_totals(booking)
    line = invoice.callout_line_parts(booking, totals)
    assert line is not None
    assert line["quantity"] == 0.75
    assert line["unit_amount"] == 180.0
    assert line["amount"] == 135.0
    assert "45 min" in line["description"]
    return True


def main() -> int:
    tests = [
        test_fee_amount_presets,
        test_staff_callout_hours_from_minutes,
        test_paid_hours_actual_break_callout,
        test_validator_auto_fee_and_minutes,
        test_legacy_infer_and_backfill,
        test_invoice_callout_line_qty,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS:", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", fn.__name__, exc)
    print("{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
