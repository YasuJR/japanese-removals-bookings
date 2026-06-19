#!/usr/bin/env python3
"""Phase 15 E2E — profit calculation tests A–E."""

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase15"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import booking_profit
import database as db
import invoice
import services


def _move_date_today() -> str:
    return date.today().isoformat()


def _create_profit_booking(
    label: str,
    *,
    status: str = "Paid",
    move_date: str = "",
) -> int:
    move_day = move_date or _move_date_today()
    booking_id = db.create_booking(
        "{0} Customer".format(label),
        "0412000333",
        "{0}@example.com".format(label.lower().replace(" ", "")),
        "10 Profit St, Perth WA",
        "20 Margin Ave, Fremantle WA",
        move_day,
        2,
        "Phase 15 {0}".format(label),
        start_time="09:00",
        finish_time="12:00",
        duration_hours="3",
        hourly_rate=110.0,
        callout_fee=0.0,
        gst_enabled=1,
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
        status=status,
    )
    return booking_id


def _set_costs(booking_id: int, **costs) -> None:
    db.update_booking_profit_fields(booking_id, costs)


def _metrics(booking_id: int) -> dict:
    row = dict(db.get_booking(booking_id))
    row["extra_charges"] = db.list_extra_charges(booking_id)
    return booking_profit.calculate_booking_profit(row)


def _month_summary(**kwargs) -> dict:
    month_key = date.today().strftime("%Y-%m")
    return booking_profit.build_monthly_profit_summary(month_key, **kwargs)


def test_a() -> dict:
    booking_id = _create_profit_booking("BankTransferA", status="Paid")
    invoice.set_payment_status(booking_id, True)
    db.update_booking_status(booking_id, "Paid")
    _set_costs(
        booking_id,
        staff_cost=100.0,
        fuel_cost=50.0,
        truck_cost=80.0,
        other_costs=0.0,
    )
    booking_profit.recalculate_and_save(booking_id)
    metrics = _metrics(booking_id)

    expected_revenue = 330.0
    expected_gst = round(expected_revenue / 11.0, 2)
    expected_net = round(expected_revenue - expected_gst, 2)
    expected_profit = round(expected_net - 100 - 50 - 80, 2)
    expected_margin = round(expected_profit / expected_revenue * 100.0, 2)

    checks = [
        ("revenue", metrics["revenue"], expected_revenue),
        ("gst_amount", metrics["gst_amount"], expected_gst),
        ("net_revenue", metrics["net_revenue"], expected_net),
        ("stripe_fee", metrics["stripe_fee"], 0.0),
        ("estimated_profit", metrics["estimated_profit"], expected_profit),
        ("profit_margin_percent", metrics["profit_margin_percent"], expected_margin),
    ]
    failed = [
        "{0}: got {1}, expected {2}".format(name, got, exp)
        for name, got, exp in checks
        if round(float(got), 2) != round(float(exp), 2)
    ]
    return {
        "name": "Test A — Paid bank transfer with staff/fuel/truck costs",
        "pass": not failed,
        "details": failed or ["Profit calculated correctly for bank transfer booking."],
        "booking_id": booking_id,
        "metrics": metrics,
    }


def test_b() -> dict:
    booking_id = _create_profit_booking("StripeCardB", status="Paid")
    db.update_booking_invoice_fields(
        booking_id,
        {
            "payment_status": invoice.PAYMENT_STATUS_PAID,
            "stripe_payment_intent_id": "pi_test_phase15",
            "stripe_payment_status": "paid",
            "stripe_surcharge_amount": 9.90,
            "stripe_total_charged": 339.90,
        },
    )
    _set_costs(
        booking_id,
        staff_cost=100.0,
        fuel_cost=50.0,
        truck_cost=80.0,
        other_costs=0.0,
    )
    booking_profit.recalculate_and_save(booking_id)
    metrics = _metrics(booking_id)

    expected_revenue = 330.0
    expected_gst = round(expected_revenue / 11.0, 2)
    expected_net = round(expected_revenue - expected_gst, 2)
    expected_profit = round(expected_net - 100 - 50 - 80 - 9.90, 2)

    failed = []
    if round(metrics["stripe_fee"], 2) != 9.90:
        failed.append(
            "stripe_fee: got {0}, expected 9.90".format(metrics["stripe_fee"])
        )
    if round(metrics["estimated_profit"], 2) != expected_profit:
        failed.append(
            "estimated_profit: got {0}, expected {1}".format(
                metrics["estimated_profit"], expected_profit
            )
        )
    return {
        "name": "Test B — Paid Stripe card payment includes surcharge",
        "pass": not failed,
        "details": failed or ["Stripe fee included in profit calculation."],
        "booking_id": booking_id,
        "metrics": metrics,
    }


def test_c() -> dict:
    move_day = _move_date_today()
    pending_id = _create_profit_booking("PendingC", status="Pending", move_date=move_day)
    completed_id = _create_profit_booking(
        "CompletedMarkerC", status="Completed", move_date=move_day
    )
    _set_costs(completed_id, staff_cost=0, fuel_cost=0, truck_cost=0, other_costs=0)
    booking_profit.recalculate_and_save(completed_id)

    summary = _month_summary()
    ids_in_month = [
        int(r["id"])
        for r in db.list_between_dates(move_day, move_day)
        if booking_profit.is_included_in_monthly_summary(dict(r))
    ]

    failed = []
    if pending_id in ids_in_month:
        failed.append("Pending booking was included in monthly summary.")
    if completed_id not in ids_in_month:
        failed.append("Completed booking was excluded from monthly summary.")
    return {
        "name": "Test C — Pending excluded from monthly profit summary",
        "pass": not failed,
        "details": failed or ["Pending booking excluded by default."],
        "pending_id": pending_id,
        "completed_id": completed_id,
        "summary_booking_count": summary["booking_count"],
    }


def test_d() -> dict:
    move_day = _move_date_today()
    completed_id = _create_profit_booking("CompletedD", status="Completed", move_date=move_day)
    booking_profit.recalculate_and_save(completed_id)
    metrics = _metrics(completed_id)
    included = booking_profit.is_included_in_monthly_summary(
        dict(db.get_booking(completed_id))
    )
    summary = _month_summary()
    failed = []
    if not included:
        failed.append("Completed booking not included in monthly summary filter.")
    if metrics["revenue"] <= 0:
        failed.append("Completed booking revenue not calculated.")
    if summary["revenue"] <= 0:
        failed.append("Monthly summary revenue is zero with completed booking present.")
    return {
        "name": "Test D — Completed booking included in monthly summary",
        "pass": not failed,
        "details": failed or ["Completed booking included with revenue counted."],
        "booking_id": completed_id,
        "metrics": metrics,
    }


def test_e() -> dict:
    booking_id = _create_profit_booking("ManualUpdateE", status="Paid")
    invoice.set_payment_status(booking_id, True)
    _set_costs(
        booking_id,
        staff_cost=100.0,
        fuel_cost=50.0,
        truck_cost=80.0,
        other_costs=0.0,
    )
    booking_profit.recalculate_and_save(booking_id)
    before = _metrics(booking_id)

    form = {
        "staff_cost": "100",
        "fuel_cost": "50",
        "truck_cost": "80",
        "other_costs": "50",
        "profit_crew_hours": "",
        "profit_hourly_wage": "",
    }
    services.save_profit_costs(booking_id, form)
    after = _metrics(booking_id)
    row = dict(db.get_booking(booking_id))

    failed = []
    if round(after["other_costs"], 2) != 50.0:
        failed.append("other_costs not saved.")
    if round(after["estimated_profit"], 2) != round(before["estimated_profit"] - 50.0, 2):
        failed.append(
            "estimated_profit: before {0}, after {1}, expected {2}".format(
                before["estimated_profit"],
                after["estimated_profit"],
                round(before["estimated_profit"] - 50.0, 2),
            )
        )
    if round(float(row.get("estimated_profit") or 0), 2) != round(
        after["estimated_profit"], 2
    ):
        failed.append("Stored estimated_profit not updated in database.")
    return {
        "name": "Test E — Manual cost update recalculates profit",
        "pass": not failed,
        "details": failed or ["Profit recalculated after manual cost update."],
        "booking_id": booking_id,
        "before_profit": before["estimated_profit"],
        "after_profit": after["estimated_profit"],
    }


def main() -> int:
    db.init_db()
    results = [test_a(), test_b(), test_c(), test_d(), test_e()]
    payload = {
        "phase": 15,
        "feature": "profit_calculation",
        "results": results,
        "all_pass": all(r["pass"] for r in results),
    }
    out_path = RESULTS_DIR / "phase15_results.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nPhase 15 — Profit Calculation E2E\n")
    print("| Test | Result |")
    print("|------|--------|")
    for row in results:
        label = row["name"].split(" — ", 1)[0]
        status = "PASS" if row["pass"] else "FAIL"
        print("| {0} | **{1}** |".format(label, status))
        for detail in row["details"]:
            print("  - {0}".format(detail))
    print("\nOverall: {0}".format("PASS" if payload["all_pass"] else "FAIL"))
    print("Results saved to {0}".format(out_path))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
