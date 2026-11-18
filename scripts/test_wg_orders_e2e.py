#!/usr/bin/env python3
"""E2E — WG Outdoor Life orders (WG Orders page, wg_orders.py).

  A  one WG order → booking (source wg_outdoor_life, "WG - Name") on Calendar + Staff Portal
  B  four orders on one day, saved together → all saved, WG Daily View in time order
  C  same time, different trucks → no conflict (same truck → conflict, override saves)
  D  different crew → each order shows only on its own staff's Staff Portal
  E  WG Outstanding → past unpaid WG order in the WG section, not REGULAR UNPAID
  F  editing an order → Calendar / Staff Portal show the new time, address, products
  G  multi-line products → line breaks kept (DB, Staff Portal, Weekly)
Plus: duplicate warning (date + name / address / REX) with Save anyway, invoice = payment
amount (no $0 labour line), no customer SMS / Xero for WG orders, search by REX,
normal bookings unchanged, nav link.
"""

import os
import random
import re
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")
for _flag in ("GOOGLE_CALENDAR_ENABLED", "GMAIL_INBOX_ENABLED", "XERO_ENABLED", "SMS_ENABLED", "STRIPE_ENABLED", "EMAIL_ENABLED"):
    os.environ.setdefault(_flag, "false")

import auth
import calendar_data
import dashboard_data
import database as db
import invoice
import services
import wg_jobs
import wg_orders
from app import app
from dashboard_data import perth_today
from integrations import payment_reminder_automation, sms

_n = 0
_rng = random.Random()
_DAY_BASE = _rng.randint(200, 3000)  # future days no earlier run has used
PRODUCTS = "1 x Litestone Harper Dining Table\n1 x Aqua Mix Sealer\n1 x Grove Coffee Table White"


def _tag():
    global _n
    _n += 1
    return "{0}-{1}".format(os.getpid(), _n)


def _client():
    db.init_db()
    label = "wg-{0}".format(_tag())
    uid = db.create_staff_user(label, auth.hash_password("t"), "WG Test", is_admin=1)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _crew(prefix="WGCrew"):
    db.init_db()
    name = "{0}-{1}".format(prefix, _tag())
    return db.create_crew_member(name, role="Mover", active=1), name


def _truck(label="T"):
    """A truck only this test uses, so other bookings on the same day never overlap it."""
    name = "WGTruck-{0}-{1}".format(label, _tag())
    db.create_truck(name)
    return name


def _rex():
    return "REX {0}".format(_rng.randint(100000, 999999))


def _day(offset=0):
    """Today for 0 / negative offsets (Staff Portal "today"), else a fresh future date."""
    if offset > 0:
        offset += _DAY_BASE
    return (perth_today() + timedelta(days=offset)).isoformat()


def _row(n, **values):
    return {"orders-{0}-{1}".format(n, key): value for key, value in values.items()}


def _post(client, day, rows, url="/wg-orders"):
    data = {"day": day}
    crew_lists = {}
    for n, row in enumerate(rows):
        row = dict(row)
        crew = row.pop("crew", [])
        data.update(_row(n, **row))
        crew_lists["orders-{0}-crew".format(n)] = crew
    payload = list(data.items())
    for key, names in crew_lists.items():
        payload.extend((key, name) for name in names)
    from werkzeug.datastructures import MultiDict

    return client.post(url, data=MultiDict(payload), follow_redirects=True)


def _order(customer, day, start, finish="", **extra):
    base = {
        "customer": customer,
        "move_date": day,
        "start_time": start,
        "finish_time": finish,
        "address": extra.pop("address", "{0} Cassinia Rd, Duncraig".format(_tag())),
        "phone": "0488 998 688",
        "payment_amount": "320",
        "payment_type": "Invoice",
        "reference": extra.pop("reference", _rex()),
        "products": PRODUCTS,
        "notes": "Parking at front\nDeliver, install and RR.",
        "truck": "",
    }
    base.update(extra)
    if not base["truck"]:
        base["truck"] = _truck()
    return base


def _wg_bookings(day):
    return [dict(r) for r in db.list_by_date(day) if wg_jobs.is_wg_order(dict(r))]


def _by_name(day, customer):
    rows = [r for r in _wg_bookings(day) if r["customer_name"] == "WG - " + customer]
    assert len(rows) == 1, (customer, [r["customer_name"] for r in _wg_bookings(day)])
    return rows[0]


def _card(html, name):
    start = html.index(name)
    return html[max(0, start - 1500): start + 3500]


def test_case_a_single_order_everywhere():
    client = _client()
    crew_id, crew_name = _crew()
    day = _day(0)
    name = "Jacob Carmody {0}".format(_tag())
    rex = _rex()
    resp = _post(client, day, [_order(name, day, "10:00", "11:30", crew=[crew_name], reference=rex)])
    assert "Saved 1 WG order" in resp.get_data(as_text=True)
    row = _by_name(day, name)
    assert row["source"] == wg_jobs.WG_ORDER_SOURCE and row["wg_reference"] == rex
    assert row["delivery_address"].endswith("Cassinia Rd, Duncraig") and row["crew"] == crew_name
    assert float(row["wg_payment_amount"]) == 320 and row["wg_payment_type"] == "Invoice"
    assert row["status"] == "Confirmed"
    # Calendar event: WG badge
    event = calendar_data.calendar_event(row)
    assert event["is_wg"] and event["customer_name"] == "WG - " + name
    cal = client.get("/calendar?view=day&date={0}".format(day)).get_data(as_text=True)
    if "WG - " + name in cal:
        assert '<span class="cal-wg-badge">WG</span> WG - ' + name in cal
    # Staff Portal card
    html = app.test_client().get("/staff?staff_id={0}&range=today".format(crew_id)).get_data(as_text=True)
    card = _card(html, "WG - " + name)
    for text in ("WG OUTDOOR LIFE", "Products:", "1 x Aqua Mix Sealer", "$320 · Invoice",
                 "Deliver, install and RR.", ">Map<", ">Call<", "Address:", rex):
        assert text in card, text
    assert "Pickup:" not in card
    # Invoice total = payment amount, single WG line, no $0 labour line
    booking = services.booking_to_dict(db.get_booking(row["id"]))
    totals = invoice.calculate_invoice_totals(booking)
    assert totals["total"] == 320.0
    assert invoice.invoice_labour_lines(booking, totals) == []
    assert [c["description"] for c in booking["extra_charges"]] == ["WG Outdoor Life delivery — " + rex]
    preview = client.get("/bookings/{0}/invoice/preview".format(row["id"])).get_data(as_text=True)
    assert "WG Outdoor Life delivery — " + rex in preview and "Moving Labour" not in preview
    return True


def test_case_b_four_orders_one_day_time_order():
    client = _client()
    _, crew_name = _crew()
    t1, t2, t3 = _truck("1"), _truck("2"), _truck("3")
    day = _day(60)
    tag = _tag()
    rows = [
        _order("Jamie Dodd " + tag, day, "13:00", "15:00", truck=t3, crew=[crew_name]),
        _order("Jacob Carmody " + tag, day, "10:00", "11:30", truck=t1),
        _order("John Webb " + tag, day, "12:00", "13:30", truck=t2, reference="{0} AND {1}".format(_rex(), _rex())),
        _order("Paul Dever " + tag, day, "11:30", "13:30", truck=t1),
    ]
    rows.insert(2, {"customer": "", "move_date": day, "payment_type": "Invoice"})  # untouched extra row is ignored
    resp = _post(client, day, rows)
    page = resp.get_data(as_text=True)
    assert "Saved 4 WG orders" in page, re.findall(r"Saved[^<]*|not saved[^<]*", page)
    view = wg_orders.day_orders(day)
    names = [d["customer"] for d in view if d["customer"].endswith(tag)]
    assert names == ["Jacob Carmody " + tag, "Paul Dever " + tag, "John Webb " + tag, "Jamie Dodd " + tag]
    html = client.get("/wg-orders?date={0}".format(day)).get_data(as_text=True)
    positions = [html.index(n) for n in names]
    assert positions == sorted(positions)
    assert "10:00 AM – 11:30 AM" in html and " AND REX " in html and "$320" in html
    return True


def test_case_c_same_time_different_truck_no_conflict():
    client = _client()
    t1, t2 = _truck("1"), _truck("2")
    day = _day(61)
    tag = _tag()
    resp = _post(client, day, [
        _order("Truck One " + tag, day, "09:00", "11:00", truck=t1),
        _order("Truck Two " + tag, day, "09:00", "11:00", truck=t2),
    ])
    assert "Saved 2 WG orders" in resp.get_data(as_text=True)
    # Same truck at the same time → not saved until overridden
    resp = _post(client, day, [_order("Truck One Again " + tag, day, "09:30", "10:30", truck=t1)])
    page = resp.get_data(as_text=True)
    assert "not saved" in page and "I understand this may be double booked" in page
    assert not [r for r in _wg_bookings(day) if r["customer_name"].startswith("WG - Truck One Again")]
    resp = _post(client, day, [_order("Truck One Again " + tag, day, "09:30", "10:30", truck=t1, override="on")])
    assert "Saved 1 WG order" in resp.get_data(as_text=True)
    assert _by_name(day, "Truck One Again " + tag)["double_booking_override_at"]
    return True


def test_case_d_crew_only_sees_own_orders():
    client = _client()
    katsu_id, katsu = _crew("Katsu")
    will_id, will = _crew("Will")
    day = _day(0)
    tag = _tag()
    _post(client, day, [
        _order("Katsu Job " + tag, day, "07:00", "08:00", crew=[katsu]),
        _order("Will Job " + tag, day, "07:00", "08:00", crew=[will]),
    ])
    k = app.test_client().get("/staff?staff_id={0}&range=today".format(katsu_id)).get_data(as_text=True)
    w = app.test_client().get("/staff?staff_id={0}&range=week".format(will_id)).get_data(as_text=True)
    assert "WG - Katsu Job " + tag in k and "WG - Will Job " + tag not in k
    assert "WG - Will Job " + tag in w and "WG - Katsu Job " + tag not in w
    both = app.test_client().get("/staff?staff_id=all&range=today").get_data(as_text=True)
    assert "WG - Katsu Job " + tag in both and "WG - Will Job " + tag in both
    return True


def test_case_e_wg_outstanding_separate_from_regular_unpaid():
    client = _client()
    past = _day(-20)
    tag = _tag()
    _post(client, past, [_order("Outstanding " + tag, past, "10:00", "11:00", payment_amount="440")])
    row = _by_name(past, "Outstanding " + tag)
    today_iso = perth_today().isoformat()
    assert dashboard_data.dashboard_section_group(row, today_iso) == dashboard_data.SECTION_WG_UNPAID
    regular = db.create_booking("Regular Unpaid " + tag, "0400000000", "", "1 A St", "2 B St", past, 2, "",
                                hourly_rate=180, start_time="10:00", finish_time="11:00")
    assert dashboard_data.dashboard_section_group(dict(db.get_booking(regular)), today_iso) == dashboard_data.SECTION_UNPAID
    summaries = dashboard_data.dashboard_unpaid_summaries([row, dict(db.get_booking(regular))], today_iso)
    assert summaries["wg"] == {"count": 1, "total": 440.0} and summaries["regular"]["count"] == 1
    # No customer payment reminders / SMS for WG orders; normal bookings unaffected
    booking = services.booking_to_dict(db.get_booking(row["id"]))
    assert payment_reminder_automation.is_eligible(booking) is False
    assert sms.send_booking_notification(booking, event="created") is None
    html = client.get("/dashboard").get_data(as_text=True)
    if "WG - Outstanding " + tag in html:
        assert html.index("WG OUTDOOR LIFE") < html.index("WG - Outstanding " + tag)
    return True


def test_case_f_edit_updates_calendar_and_staff_portal():
    client = _client()
    crew_id, crew_name = _crew()
    day = _day(0)
    name = "Edit Me " + _tag()
    rex = _rex()
    new_address = "{0} Changed Ave, Hillarys".format(_rng.randint(1000, 99999))
    _post(client, day, [_order(name, day, "14:00", "15:00", crew=[crew_name])])
    row = _by_name(day, name)
    form = _order(name, day, "15:30", "17:00", crew=[crew_name], address=new_address,
                  products="2 x New Chair", payment_amount="510", reference=rex, notes="New note",
                  truck=row["truck_assigned"])
    resp = _post(client, day, [form], url="/wg-orders/{0}/edit".format(row["id"]))
    assert "Saved 1 WG order" in resp.get_data(as_text=True)
    after = dict(db.get_booking(row["id"]))
    assert (after["start_time"], after["finish_time"], after["delivery_address"]) == ("15:30", "17:00", new_address)
    assert after["wg_products"] == "2 x New Chair" and float(after["wg_payment_amount"]) == 510 and after["truck_assigned"] == row["truck_assigned"]
    assert len(_wg_bookings(day)) == len({r["id"] for r in _wg_bookings(day)})
    assert [r for r in _wg_bookings(day) if r["customer_name"] == "WG - " + name][0]["id"] == row["id"], "edit, not a new booking"
    assert invoice.calculate_invoice_totals(services.booking_to_dict(db.get_booking(row["id"])))["total"] == 510.0
    event = calendar_data.calendar_event(after)
    assert event["start_time"] == "15:30"
    html = app.test_client().get("/staff?staff_id={0}&range=today".format(crew_id)).get_data(as_text=True)
    card = _card(html, "WG - " + name)
    assert new_address in card and "2 x New Chair" in card and "$510" in card and rex in card
    assert "3:30 PM" in card
    edit_page = client.get("/wg-orders/{0}/edit".format(row["id"])).get_data(as_text=True)
    assert 'value="{0}"'.format(new_address) in edit_page and "Save WG Order" in edit_page
    return True


def test_case_g_products_keep_line_breaks():
    client = _client()
    crew_id, crew_name = _crew()
    day = _day(0)
    name = "Lines " + _tag()
    _post(client, day, [_order(name, day, "16:00", "17:00", crew=[crew_name], products=PRODUCTS.replace("\n", "\r\n"))])
    row = _by_name(day, name)
    assert row["wg_products"] == PRODUCTS
    html = app.test_client().get("/staff?staff_id={0}&range=today".format(crew_id)).get_data(as_text=True)
    assert '<p class="staff-wg-products">' + PRODUCTS + "</p>" in html
    weekly = app.test_client().get("/staff?staff_id={0}&range=week".format(crew_id)).get_data(as_text=True)
    assert PRODUCTS in weekly
    edit_page = client.get("/wg-orders/{0}/edit".format(row["id"])).get_data(as_text=True)
    assert ">" + PRODUCTS + "</textarea>" in edit_page
    return True


def test_duplicate_warning_and_save_anyway():
    client = _client()
    day = _day(62)
    tag = _tag()
    _post(client, day, [_order("Dup " + tag, day, "08:00", "09:00", reference=_rex())])
    ref = _by_name(day, "Dup " + tag)["wg_reference"]
    # Different name/address but the same REX number → warning, not saved
    resp = _post(client, day, [_order("Other " + tag, day, "12:00", "13:00", reference=ref, address="1 Other St, Perth")])
    page = resp.get_data(as_text=True)
    assert "Possible duplicate" in page and "same REX" in page and "Save anyway" in page
    assert not [r for r in _wg_bookings(day) if r["customer_name"] == "WG - Other " + tag]
    resp = _post(client, day, [_order("Other " + tag, day, "12:00", "13:00", reference=ref, address="1 Other St, Perth",
                                      force_duplicate="on")])
    assert "Saved 1 WG order" in resp.get_data(as_text=True)
    assert wg_orders.reference_numbers("REX 1649 AND REX 2359") == {"1649", "2359"}
    return True


def test_validation_and_normal_bookings_unchanged():
    client = _client()
    day = _day(63)
    resp = _post(client, day, [{"customer": "No Address", "move_date": day, "start_time": "10:00", "finish_time": "09:00"}])
    page = resp.get_data(as_text=True)
    assert "Address is required." in page and "Finish time must be after start time." in page
    normal = db.create_booking("Normal Customer " + _tag(), "0400111222", "n@example.com", "1 A St", "2 B St", day, 2, "",
                               hourly_rate=180, start_time="08:00", finish_time="10:00")
    b = services.booking_to_dict(db.get_booking(normal))
    assert not wg_jobs.is_wg_order(b) and len(invoice.invoice_labour_lines(b)) == 1
    assert calendar_data.calendar_event(b)["is_wg"] is False
    assert "WG Orders" in client.get("/dashboard").get_data(as_text=True)
    # Search finds a WG order by its REX number
    rex = _rex()
    _post(client, day, [_order("Searchable " + _tag(), day, "11:00", "12:00", reference=rex)])
    hits = [r["customer_name"] for r in db.search_bookings(rex)]
    assert any(h.startswith("WG - Searchable") for h in hits), hits
    return True


def main():
    tests = [
        test_case_a_single_order_everywhere,
        test_case_b_four_orders_one_day_time_order,
        test_case_c_same_time_different_truck_no_conflict,
        test_case_d_crew_only_sees_own_orders,
        test_case_e_wg_outstanding_separate_from_regular_unpaid,
        test_case_f_edit_updates_calendar_and_staff_portal,
        test_case_g_products_keep_line_breaks,
        test_duplicate_warning_and_save_anyway,
        test_validation_and_normal_bookings_unchanged,
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
