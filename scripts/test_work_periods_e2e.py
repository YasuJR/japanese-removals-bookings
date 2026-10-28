#!/usr/bin/env python3
"""E2E — Booking Edit work periods (Period 2 with a different number of movers).

Cases from the spec:
  A  one period (8:00–15:00, 2 movers) → unchanged invoice
  B  8:00–12:00 × 3 movers + 12:00–15:00 × 2 movers → two labour lines, correct total
  C  removing Period 2 → back to one period
  D  call out charged once
  E  break deducted at the rate of the period it was taken in
  F  existing bookings (no Period 2, saved actual times) keep their display, invoice and Paid Hours
Plus: Actual start/finish/duration are gone from Booking Edit, forms without the work-period
section never clear Period 2, invoice preview / PDF / Xero lines, staff paid hours unchanged.
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import invoice
import mover_pricing
import services
import staff_job_times
import work_periods
from app import app
from integrations import invoice_pdf
from integrations import xero as xero_mod

RATE_3 = mover_pricing.hourly_rate_for_movers(3)
RATE_2 = mover_pricing.hourly_rate_for_movers(2)
_counter = 0


def _client():
    global _counter
    _counter += 1
    db.init_db()
    label = "work-period-{0}-{1}".format(os.getpid(), _counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Work Period Test", is_admin=1)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _booking(movers=3, start="08:00", finish="12:00", rate=None, callout_minutes=0, status="Confirmed"):
    rate = rate if rate is not None else mover_pricing.hourly_rate_for_movers(movers)
    return db.create_booking(
        "Work Period Customer {0}".format(_counter),
        "0412000777",
        "work-period@example.com",
        "1 Period St, Perth WA",
        "2 Period Ave, Fremantle WA",
        "2026-09-14",
        movers,
        "work period test",
        hourly_rate=rate,
        callout_fee=round(rate * callout_minutes / 60, 2),
        callout_minutes=callout_minutes,
        gst_enabled=1,
        start_time=start,
        finish_time=finish,
        duration_hours="",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
        crew="Yasu",
        status=status,
    )


def _form(booking_id, **overrides):
    row = dict(db.get_booking(booking_id))
    base = {
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "email": row["email"],
        "pickup_address": row["pickup_address"],
        "delivery_address": row["delivery_address"],
        "move_date": row["move_date"],
        "num_movers": str(row["num_movers"]),
        "notes": row["notes"] or "",
        "start_time": row["start_time"],
        "finish_time": row["finish_time"],
        "duration_hours": "",
        "hourly_rate": str(row["hourly_rate"]),
        "callout_minutes": str(row["callout_minutes"] or 0),
        "gst_enabled": "on",
        "payment_status": row["payment_status"] or "Unpaid",
        "invoice_status": row["invoice_status"] or "",
        "status": row["status"] or "Confirmed",
        "crew": "Yasu",
        "work_period_2_enabled": "0",
        "action": "save",
        "double_booking_override_confirm": "on",
    }
    base.update(overrides)
    return base


def _period_two_form(start="12:00", finish="15:00", movers="2", rate=""):
    return {
        "work_period_2_enabled": "1",
        "work_period_2_start": start,
        "work_period_2_finish": finish,
        "work_period_2_movers": movers,
        "work_period_2_rate": rate,
    }


def _totals(booking_id):
    return invoice.calculate_invoice_totals(services.booking_to_dict(db.get_booking(booking_id)))


def _save(client, booking_id, **overrides):
    resp = client.post("/bookings/{0}/edit".format(booking_id), data=_form(booking_id, **overrides))
    assert resp.status_code in (200, 302), resp.status_code
    return resp


def test_edit_page_has_no_actual_fields_and_offers_period_two():
    client = _client()
    bid = _booking()
    html = client.get("/bookings/{0}/edit".format(bid)).get_data(as_text=True)
    for gone in ('name="actual_start_time"', 'name="actual_finish_time"', "Actual duration", "Actual start time"):
        assert gone not in html, gone
    assert 'id="work-period-2-add"' in html and "Add another work period" in html and "作業時間を追加" in html
    assert re.search(r'<fieldset class="work-period-2" id="work-period-2"\s+hidden', html), "Period 2 hidden by default"
    assert 'name="work_period_2_enabled" id="work_period_2_enabled" value="0"' in html
    assert "Additional Work Period" in html and "追加作業時間" in html
    return True


def test_case_a_single_period_unchanged():
    client = _client()
    bid = _booking(movers=2, start="08:00", finish="15:00")
    before = _totals(bid)
    _save(client, bid)
    after = _totals(bid)
    assert after["total"] == before["total"] == round(7 * RATE_2, 2), (before["total"], after["total"])
    assert len(after["labour_lines"]) == 1
    labour = invoice.invoice_labour_lines(services.booking_to_dict(db.get_booking(bid)), after)
    assert labour[0]["description"] == "Moving Labour — 8:00 AM - 3:00 PM — 7 hrs @ $180.00/hr", labour[0]["description"]
    row = dict(db.get_booking(bid))
    assert not row.get("work_period_2_start") and row.get("work_period_2_movers") is None
    return True


def test_case_b_two_periods_two_lines_and_total():
    client = _client()
    bid = _booking(movers=3, start="08:00", finish="12:00")
    _save(client, bid, **_period_two_form())
    row = dict(db.get_booking(bid))
    assert row["work_period_2_start"] == "12:00" and row["work_period_2_finish"] == "15:00"
    assert int(row["work_period_2_movers"]) == 2
    assert float(row["work_period_2_rate"]) == RATE_2  # standard 2-mover rate, not typed in
    totals = _totals(bid)
    expected_labour = round(4 * RATE_3 + 3 * RATE_2, 2)
    assert totals["labour_amount"] == expected_labour == 1480.0, totals["labour_amount"]
    assert totals["total"] == expected_labour
    booking = services.booking_to_dict(db.get_booking(bid))
    lines = invoice.invoice_labour_lines(booking, totals)
    assert [l["description"] for l in lines] == [
        "Moving Labour — 8:00 AM - 12:00 PM — 4 hrs × 3 movers @ $235.00/hr",
        "Moving Labour — 12:00 PM - 3:00 PM — 3 hrs × 2 movers @ $180.00/hr",
    ], [l["description"] for l in lines]
    assert [l["amount"] for l in lines] == [940.0, 540.0]

    # Edit page shows Period 2 open with its values
    html = client.get("/bookings/{0}/edit".format(bid)).get_data(as_text=True)
    assert 'name="work_period_2_enabled" id="work_period_2_enabled" value="1"' in html
    assert 'value="12:00"' in html and 'value="15:00"' in html and 'value="180.00"' in html

    # Invoice preview + PDF line items
    doc = invoice_pdf.build_invoice_document(booking)
    labour_items = [i for i in doc["line_items"] if "Moving Labour" in i["description_html"]]
    assert len(labour_items) == 2
    assert labour_items[0]["amount"] == "$940.00" and labour_items[1]["amount"] == "$540.00"
    assert labour_items[0]["unit_price"] == "$235.00" and labour_items[1]["unit_price"] == "$180.00"
    preview = client.get("/bookings/{0}/invoice/preview".format(bid)).get_data(as_text=True)
    assert "4 hrs × 3 movers @ $235.00/hr" in preview and "3 hrs × 2 movers @ $180.00/hr" in preview
    assert "$1,480.00" in preview
    pdf = client.get("/bookings/{0}/invoice.pdf".format(bid))
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"

    # Xero draft lines
    xero_items = xero_mod._build_line_items(booking, totals)
    assert [(i["Quantity"], i["UnitAmount"]) for i in xero_items[:2]] == [(4.0, RATE_3), (3.0, RATE_2)]
    assert "3 hrs × 2 movers" in xero_items[1]["Description"]

    # Live calculation endpoint used by the edit panel
    calc = client.post("/bookings/{0}/invoice/calculate".format(bid), data=_form(bid, **_period_two_form())).get_json()
    assert calc["labour_total"] == 1480.0 and len(calc["labour_lines"]) == 2, calc
    return True


def test_case_c_remove_period_two():
    client = _client()
    bid = _booking(movers=3, start="08:00", finish="12:00")
    _save(client, bid, **_period_two_form())
    assert len(_totals(bid)["labour_lines"]) == 2
    _save(client, bid, work_period_2_enabled="0")
    row = dict(db.get_booking(bid))
    assert not row["work_period_2_start"] and row["work_period_2_movers"] is None and row["work_period_2_rate"] is None
    totals = _totals(bid)
    assert len(totals["labour_lines"]) == 1 and totals["total"] == round(4 * RATE_3, 2)
    return True


def test_case_d_callout_once():
    client = _client()
    bid = _booking(movers=3, start="08:00", finish="12:00", callout_minutes=45)
    _save(client, bid, callout_minutes="45", **_period_two_form())
    totals = _totals(bid)
    callout = round(RATE_3 * 45 / 60, 2)
    assert totals["callout_fee"] == callout
    assert totals["total"] == round(1480 + callout, 2), (totals["total"], callout)
    booking = services.booking_to_dict(db.get_booking(bid))
    doc = invoice_pdf.build_invoice_document(booking)
    callout_items = [i for i in doc["line_items"] if "allout" in i["description_html"]]
    assert len(callout_items) == 1 and "45 min" in callout_items[0]["description_html"]
    assert callout_items[0]["amount"] == invoice.format_aud(callout)
    # Unsaved preview (Preview Invoice button) shows the minutes typed in the form.
    preview = client.post(
        "/bookings/{0}/invoice/preview".format(bid), data=_form(bid, callout_minutes="60", **_period_two_form())
    ).get_data(as_text=True)
    assert "Callout fee (60 min)" in preview and preview.count("Callout fee") == 1, "callout once, with form minutes"
    return True


def test_case_e_break_priced_by_period():
    client = _client()
    bid = _booking(movers=3, start="08:00", finish="12:00")
    p2_break = round(-RATE_2 * 30 / 60, 2)
    _save(
        client,
        bid,
        extra_description="30 min break deduction (Period 2)",
        extra_quantity="1",
        extra_unit_price=str(p2_break),
        **_period_two_form()
    )
    totals = _totals(bid)
    assert totals["total"] == round(1480 + p2_break, 2) == 1390.0, totals["total"]
    booking = services.booking_to_dict(db.get_booking(bid))
    # Staff paid hours read the minutes from the description, independent of the rate.
    import extra_charges

    assert extra_charges.break_hours_from_booking(booking) == 0.5
    return True


def test_case_f_existing_booking_and_saved_actuals_untouched():
    client = _client()
    bid = _booking(movers=2, start="08:00", finish="12:00", status="Completed")
    db.save_booking_actual_times(bid, "08:10", "12:40", 270)
    booking = services.booking_to_dict(db.get_booking(bid))
    paid_before = staff_job_times.paid_hours(booking)
    total_before = invoice.calculate_invoice_totals(booking)["total"]

    html = client.get("/bookings/{0}/edit".format(bid)).get_data(as_text=True)
    assert "Recorded actual time 8:10 AM – 12:40 PM" in html and "Staff Paid Hours only" in html
    assert "Moving Labour — 8:00 AM - 12:00 PM — 4 hrs @ $180.00/hr" in client.get(
        "/bookings/{0}/invoice/preview".format(bid)
    ).get_data(as_text=True)

    # Saving Booking Edit (which no longer has actual fields) keeps the actual times.
    _save(client, bid)
    row = dict(db.get_booking(bid))
    assert (row["actual_start_time"], row["actual_finish_time"], row["actual_duration"]) == ("08:10", "12:40", 270)
    booking = services.booking_to_dict(db.get_booking(bid))
    assert staff_job_times.paid_hours(booking) == paid_before
    assert invoice.calculate_invoice_totals(booking)["total"] == total_before

    # A Period 2 does not change Staff Paid Hours (billing only).
    _save(client, bid, **_period_two_form(movers="3"))
    booking = services.booking_to_dict(db.get_booking(bid))
    assert staff_job_times.paid_hours(booking) == paid_before
    assert invoice.calculate_invoice_totals(booking)["total"] == round(4 * RATE_2 + 3 * RATE_3, 2)
    return True


def test_forms_without_section_keep_period_two_and_validation():
    client = _client()
    bid = _booking(movers=3, start="08:00", finish="12:00")
    _save(client, bid, **_period_two_form())
    form = _form(bid)
    form.pop("work_period_2_enabled")
    client.post("/bookings/{0}/edit".format(bid), data=form)
    assert dict(db.get_booking(bid))["work_period_2_start"] == "12:00", "Period 2 must not be cleared"

    # Validation: overlap with Period 1, finish before start, missing rate for a non-standard size
    for bad, message in (
        (_period_two_form(start="11:00"), "no overlap"),
        (_period_two_form(start="15:00", finish="13:00"), "finish time must be after start"),
        (_period_two_form(movers="5"), "no standard rate for 5 movers"),
    ):
        _, errors = __import__("validators").parse_booking_form(_form(bid, **bad))
        assert any(message in e for e in errors), (bad, errors)
    # Non-standard size works with a typed rate
    _save(client, bid, **_period_two_form(movers="5", rate="400"))
    assert _totals(bid)["labour_amount"] == round(4 * RATE_3 + 3 * 400, 2)
    return True


def test_period_two_helpers():
    assert work_periods.period_two({}) is None
    assert work_periods.period_two({"work_period_2_start": "12:00", "work_period_2_finish": "15:00", "work_period_2_movers": 2})["rate"] == RATE_2
    assert work_periods.period_two({"work_period_2_start": "12:00", "work_period_2_finish": "11:00", "work_period_2_movers": 2}) is None
    return True


def main():
    tests = [
        test_edit_page_has_no_actual_fields_and_offers_period_two,
        test_case_a_single_period_unchanged,
        test_case_b_two_periods_two_lines_and_total,
        test_case_c_remove_period_two,
        test_case_d_callout_once,
        test_case_e_break_priced_by_period,
        test_case_f_existing_booking_and_saved_actuals_untouched,
        test_forms_without_section_keep_period_two_and_validation,
        test_period_two_helpers,
    ]
    passed = 0
    for test in tests:
        try:
            if test():
                print("PASS:", test.__name__)
                passed += 1
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            print("FAIL:", test.__name__, "—", exc)
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
