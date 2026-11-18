#!/usr/bin/env python3
"""E2E — WG Days (WG Orders page, wg_orders.py): one booking per day + its delivery stops.

  A  one day with 4 stops → one WG booking; Dashboard shows "WG Outdoor Life · 4 stops", no customers
  B  Calendar → one WG OUTDOOR LIFE entry, "4 stops", first stop start → last stop finish
  C  Crew set once on the WG Day (no per-stop crew) and applied to the whole day
  D  Truck set once; a second WG Day on the same date is refused; same-truck overlap needs override
  E  Staff Portal → the day is one job: WG OUTDOOR LIFE, date, truck, 4 STOPS, View WG Run Sheet
  F  View WG Run Sheet → all 4 stops in time order with every field
  G  Run Sheet PDF → all 4 stops in one PDF, in order
  H  Map (Google Maps) / Call (tel:) on every stop
  J  normal bookings unchanged (invoice, calendar, staff card, search)
Plus: edit (same booking, stops re-sorted, delete stop), in-day duplicates need "Save anyway",
validation saves nothing, WG Outstanding section, hourly invoice (stop payments are not
invoice lines), no customer SMS / reminders, older per-customer bookings move into the day.
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

from werkzeug.datastructures import MultiDict

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
_used_days = set()
PRODUCTS = "1 x Litestone Harper Dining Table\n1 x Aqua Mix Sealer\n1 x Grove Coffee Table White"
TEST_PREFIX = "WGT "  # every stop customer in this file starts with it (cleanup of "today")


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


def _free_day(past=False):
    """A date with no WG Day yet (one WG Day per date)."""
    while True:
        offset = _rng.randint(30, 400) if past else _rng.randint(200, 4000)
        day = (perth_today() + timedelta(days=-offset if past else offset)).isoformat()
        if day not in _used_days and wg_orders.find_day(day) is None:
            _used_days.add(day)
            return day


def _clear_test_day_today():
    """Remove a WG Day this file left on today's date (Staff Portal "Today" needs today)."""
    today = perth_today().isoformat()
    day = wg_orders.find_day(today)
    if day is None:
        return today
    stops = db.list_wg_stops(int(day["id"]))
    if all(str(s["customer"]).startswith(TEST_PREFIX) for s in stops):
        db.delete_booking(int(day["id"]))
        return today
    raise RuntimeError("A real WG Day exists today in the local DB — not touching it")


def _stop(customer, start, finish="", **extra):
    base = {
        "customer": TEST_PREFIX + customer,
        "start_time": start,
        "finish_time": finish,
        "address": "{0} Cassinia Rd, Duncraig".format(_rng.randint(1, 99999)),
        "phone": "0488 998 688",
        "payment_amount": "320",
        "payment_type": "COD",
        "reference": _rex(),
        "products": PRODUCTS,
        "notes": "Parking at front\nDeliver, install and RR.",
    }
    base.update(extra)
    return base


def _post_day(client, move_date, stops, crew=(), truck="", booking_id=None, **extra):
    payload = [("day", move_date), ("move_date", move_date), ("truck", truck)]
    payload += [("crew", name) for name in crew]
    if booking_id:
        payload.append(("booking_id", str(booking_id)))
    for key, value in extra.items():
        payload.append((key, value))
    for n, stop in enumerate(stops):
        payload += [("stops-{0}-{1}".format(n, key), value) for key, value in stop.items()]
    return client.post("/wg-orders", data=MultiDict(payload), follow_redirects=True)


def _wg_rows(day):
    return [dict(r) for r in db.list_by_date(day) if wg_jobs.is_wg_outdoor_life(dict(r))]


def _four_stops(tag):
    """WG's usual sheet, typed out of time order on purpose."""
    return [
        _stop("Jamie Dodd " + tag, "13:00", "15:00", payment_amount="440", address="9 Dodd Ave, Wembley WA"),
        _stop("Jacob Carmody " + tag, "10:00", "11:30", payment_amount="320", payment_type="YASU",
              address="15 Cassinia Rd, Duncraig WA", phone="0488 998 688"),
        _stop("John Webb " + tag, "12:00", "13:30", payment_amount="220",
              reference="{0} AND {1}".format(_rex(), _rex()), address="3 Webb St, Perth WA"),
        _stop("Paul Dever " + tag, "11:30", "13:30", payment_amount="330", payment_type="Confirmed",
              address="7 Canning Hwy, East Fremantle WA", phone="0456 141 788"),
    ]


def _day_url(move_date):
    return "/calendar?view=day&year={0}&month={1}&day={2}".format(int(move_date[:4]), int(move_date[5:7]), int(move_date[8:10]))


ORDER = ("Jacob Carmody", "Paul Dever", "John Webb", "Jamie Dodd")


def _flashes(page):
    return re.findall(r'class="flash[^"]*"[^>]*>\s*([^<]*)', page)


def _make_day(client, move_date=None, crew=(), truck=None, tag=None):
    tag = tag or _tag()
    move_date = move_date or _free_day()
    truck = truck if truck is not None else _truck()
    resp = _post_day(client, move_date, _four_stops(tag), crew=crew, truck=truck)
    page = resp.get_data(as_text=True)
    assert "Saved the WG Day" in page and "4 stops" in page, _flashes(page) or page[:400]
    day = wg_orders.find_day(move_date)
    assert day is not None
    return day, tag, move_date, truck


def test_case_a_one_booking_dashboard_one_row():
    client = _client()
    day, tag, move_date, _truck_name = _make_day(client)
    rows = _wg_rows(move_date)
    assert len(rows) == 1, [r["customer_name"] for r in rows]
    b = rows[0]
    assert b["customer_name"] == "WG Outdoor Life" and b["source"] == wg_jobs.WG_ORDER_SOURCE
    assert int(b["wg_stop_count"]) == 4 and wg_jobs.is_wg_day(b)
    stops = db.list_wg_stops(int(b["id"]))
    assert [s["customer"] for s in stops] == [TEST_PREFIX + n + " " + tag for n in ORDER], "saved in time order"
    assert stops[0]["products"] == PRODUCTS and stops[0]["payment_type"] == "YASU" and float(stops[0]["payment_amount"]) == 320
    # Dashboard: TODAY & UPCOMING, one row "WG Outdoor Life · WG Day · 4 stops", no customer names
    today_iso = perth_today().isoformat()
    assert dashboard_data.dashboard_section_group(b, today_iso) == dashboard_data.SECTION_TODAY_UPCOMING
    html = client.get("/dashboard").get_data(as_text=True)
    for name in ORDER:
        assert name + " " + tag not in html
    if "/bookings/{0}\"".format(b["id"]) in html:
        assert "WG Day · 4 stops" in html
    # Invoice: the day's normal hourly invoice; stop payments are not invoice lines
    booking = services.booking_to_dict(db.get_booking(b["id"]))
    totals = invoice.calculate_invoice_totals(booking)
    assert float(booking["hourly_rate"]) == 180.0 and int(booking["callout_minutes"]) == 30
    assert totals["total"] == round(180 * 5 + 90, 2), totals["total"]  # 10:00 → 15:00 + 30 min call-out
    assert not booking["extra_charges"]
    # Booking detail lists the stops
    detail = client.get("/bookings/{0}".format(b["id"])).get_data(as_text=True)
    assert "WG Day · 4 stops" in detail and "Jacob Carmody " + tag in detail
    return True


def test_case_b_calendar_one_entry():
    client = _client()
    names = [_crew("Katsu")[1], _crew("Keiichi")[1]]
    day, tag, move_date, _t = _make_day(client, crew=names)
    event = calendar_data.calendar_event(dict(db.get_booking(day["id"])))
    assert event["is_wg_day"] and event["wg_stops_label"] == "4 stops"
    assert (event["start_display"], event["finish_display"]) == ("10:00 AM", "3:00 PM"), event["time_range"]
    assert event["edit_url"] == "/wg-orders?date=" + move_date
    cal = client.get(_day_url(move_date)).get_data(as_text=True)
    assert cal.count("WG OUTDOOR LIFE</strong>") == 1, cal.count("WG OUTDOOR LIFE</strong>")
    assert "4 stops" in cal and "Crew: " + ", ".join(names) in cal
    for name in ORDER:
        assert name + " " + tag not in cal
    month = client.get("/calendar?view=month&year={0}&month={1}".format(move_date[:4], int(move_date[5:7]))).get_data(as_text=True)
    for name in ORDER:
        assert name + " " + tag not in month
    return True


def test_case_c_crew_once_for_the_day():
    client = _client()
    (k_id, katsu), (w_id, _will) = _crew("Katsu"), _crew("Will")
    page = client.get("/wg-orders?date=" + _free_day()).get_data(as_text=True)
    assert 'name="crew"' in page and not re.search(r'name="stops-\d+-crew"', page), "crew once, not per stop"
    assert "Copy previous" not in page and "Remove Order" not in page
    assert "WG Day Crew" in page and 'id="wg-stop-add"' in page and "+ Add Stop" in page
    day, tag, move_date, _t = _make_day(client, crew=[katsu])
    assert dict(db.get_booking(day["id"]))["crew"] == katsu
    # Everyone on the day's crew sees the day; others don't
    year, month = move_date[:4], int(move_date[5:7])
    url = "/staff?staff_id={0}&range=calendar&cal_view=month&year={1}&month={2}"
    k = app.test_client().get(url.format(k_id, year, month)).get_data(as_text=True)
    w = app.test_client().get(url.format(w_id, year, month)).get_data(as_text=True)
    assert '/staff/wg/{0}"'.format(day["id"]) in k and '/staff/wg/{0}"'.format(day["id"]) not in w
    return True


def test_case_d_truck_once_one_day_per_date():
    client = _client()
    truck = _truck("D")
    day, tag, move_date, _t = _make_day(client, truck=truck)
    assert dict(db.get_booking(day["id"]))["truck_assigned"] == truck
    page = client.get("/wg-orders?date=" + move_date).get_data(as_text=True)
    assert len(re.findall(r'<select name="truck"', page)) == 1 and not re.search(r'name="stops-\d+-truck"', page)
    # A second new WG Day on the same date is refused (open the saved one instead)
    resp = _post_day(client, move_date, [_stop("Second Day " + tag, "16:00", "17:00")], truck=_truck())
    assert "There is already a WG Day on this date" in resp.get_data(as_text=True)
    assert len(_wg_rows(move_date)) == 1
    # A normal booking on the same truck at the same time → the WG Day save needs the override
    other = db.create_booking("Normal Same Truck " + tag, "", "", "1 A St", "2 B St", move_date, 2, "",
                              start_time="10:30", finish_time="11:30", hourly_rate=180, status="Confirmed")
    db.update_booking_integration_fields(other, {"truck_assigned": truck})
    stops = _four_stops(tag)
    page = _post_day(client, move_date, stops, truck=truck, booking_id=day["id"]).get_data(as_text=True)
    assert "I understand this may be double booked" in page, _flashes(page)
    page = _post_day(client, move_date, stops, truck=truck, booking_id=day["id"], override="on").get_data(as_text=True)
    assert "Saved the WG Day" in page, _flashes(page)
    assert dict(db.get_booking(day["id"]))["double_booking_override_at"]
    return True


def test_case_e_staff_portal_one_job():
    client = _client()
    crew_id, crew_name = _crew("Yasu")
    today = _clear_test_day_today()
    truck = _truck("E")
    try:
        day, tag, _d, _t = _make_day(client, move_date=today, crew=[crew_name], truck=truck)
        for url in ("/staff?staff_id={0}&range=today".format(crew_id), "/staff?staff_id=all&range=today",
                    "/staff?staff_id={0}&range=week".format(crew_id)):
            html = app.test_client().get(url).get_data(as_text=True)
            assert 'href="/staff/wg/{0}"'.format(day["id"]) in html, url
            assert "WG OUTDOOR LIFE" in html and "4 STOPS" in html and "View WG Run Sheet" in html, url
            assert truck in html and perth_today().strftime("%A %-d %B") in html, url
            assert "Download WG Run Sheet PDF" in html, url
            for name in ORDER:
                assert name + " " + tag not in html, (url, name)
    finally:
        found = wg_orders.find_day(today)
        if found:
            db.delete_booking(int(found["id"]))
    return True


def test_case_f_run_sheet_all_stops_in_order():
    client = _client()
    _, crew_name = _crew("Keiichi")
    day, tag, move_date, truck = _make_day(client, crew=[crew_name])
    html = app.test_client().get("/staff/wg/{0}".format(day["id"])).get_data(as_text=True)
    positions = [html.index(n + " " + tag) for n in ORDER]
    assert positions == sorted(positions), "time order"
    for i in range(1, 5):
        assert "STOP {0}".format(i) in html
    for text in ("10:00 AM – 11:30 AM", "11:30 AM – 1:30 PM", "12:00 PM – 1:30 PM", "1:00 PM – 3:00 PM",
                 "1 x Aqua Mix Sealer", "15 Cassinia Rd, Duncraig WA", "0488 998 688", "$320 · YASU", "$330 · Confirmed",
                 " AND REX ", "Deliver, install and RR.", crew_name, truck, "WG OUTDOOR LIFE"):
        assert text in html, text
    assert '<p class="wgr-products">' + PRODUCTS + "</p>" in html, "products keep line breaks"
    assert app.test_client().get("/staff/wg/999999999").status_code == 404
    normal = db.create_booking("Not WG " + tag, "", "", "1 A St", "2 B St", move_date, 2, "", hourly_rate=180)
    assert app.test_client().get("/staff/wg/{0}".format(normal)).status_code == 404
    return True


def test_case_g_pdf_all_stops():
    import fitz

    client = _client()
    _, crew_name = _crew("Katsu")
    day, tag, move_date, truck = _make_day(client, crew=[crew_name])
    resp = app.test_client().get("/staff/wg/{0}/run-sheet.pdf".format(day["id"]))
    assert resp.status_code == 200 and resp.mimetype == "application/pdf"
    assert "wg-run-sheet-{0}.pdf".format(move_date) in resp.headers["Content-Disposition"]
    doc = fitz.open(stream=resp.data, filetype="pdf")
    text = " ".join(" ".join(page.get_text().split()) for page in doc)
    for word in ("Japanese Removals", "WG OUTDOOR LIFE", "Crew:", crew_name, "Truck:", truck, "4 stops"):
        assert word in text, word
    positions = [text.index(n + " " + tag) for n in ORDER]
    assert positions == sorted(positions)
    stop_pos = [text.index("STOP {0}".format(i)) for i in range(1, 5)]
    assert stop_pos == sorted(stop_pos)
    for word in ("1 x Aqua Mix Sealer", "1 x Grove Coffee Table White", "15 Cassinia Rd, Duncraig WA",
                 "0456 141 788", "$320 · YASU", "REX", "Deliver, install and RR."):
        assert word in text, word
    return True


def test_case_h_map_and_call_each_stop():
    client = _client()
    day, tag, _d, _t = _make_day(client)
    html = app.test_client().get("/staff/wg/{0}".format(day["id"])).get_data(as_text=True)
    maps = re.findall(r'href="(https://www\.google\.com/maps/search/\?api=1&amp;query=[^"]+)"[^>]*>Map<', html)
    calls = re.findall(r'href="(tel:[^"]+)">Call<', html)
    assert len(maps) == 4 and len(calls) == 4, (len(maps), len(calls))
    assert "query=15%20Cassinia%20Rd%2C%20Duncraig%20WA" in maps[0]
    assert calls[0] == "tel:+61488998688" and calls[1] == "tel:+61456141788"
    return True


def test_edit_resort_and_delete_stop():
    client = _client()
    day, tag, move_date, truck = _make_day(client)
    page = client.get("/wg-orders?date=" + move_date).get_data(as_text=True)
    assert 'name="booking_id" value="{0}"'.format(day["id"]) in page and "Edit WG Day" in page
    assert page.count("data-wg-delete-stop") == 5, "small Delete stop on 4 stops + the template"
    stops = _four_stops(tag)
    # Jamie moves to the morning; John Webb is deleted (not posted)
    stops[0]["start_time"], stops[0]["finish_time"] = "08:00", "09:00"
    stops = [s for s in stops if "John Webb" not in s["customer"]]
    page = _post_day(client, move_date, stops, truck=truck, booking_id=day["id"]).get_data(as_text=True)
    assert "Saved the WG Day" in page and "3 stops" in page, _flashes(page)
    assert len(_wg_rows(move_date)) == 1
    after = dict(db.get_booking(day["id"]))
    assert int(after["wg_stop_count"]) == 3 and after["start_time"] == "08:00" and after["finish_time"] == "13:30"
    names = [s["customer"] for s in db.list_wg_stops(day["id"])]
    assert names == [TEST_PREFIX + n + " " + tag for n in ("Jamie Dodd", "Jacob Carmody", "Paul Dever")]
    # Typed day times win over the stops (invoice hours)
    _post_day(client, move_date, stops, truck=truck, booking_id=day["id"], day_start="07:30", day_finish="16:00")
    after = dict(db.get_booking(day["id"]))
    assert (after["start_time"], after["finish_time"]) == ("07:30", "16:00")
    page = client.get("/wg-orders?date=" + move_date).get_data(as_text=True)
    assert 'name="day_start" value="07:30"' in page
    return True


def test_duplicates_and_validation():
    client = _client()
    move_date = _free_day()
    tag = _tag()
    rex = _rex()
    stops = [_stop("Dup One " + tag, "09:00", "10:00", reference=rex),
             _stop("Dup Two " + tag, "11:00", "12:00", reference=rex, address="1 Other St, Perth")]
    page = _post_day(client, move_date, stops, truck=_truck()).get_data(as_text=True)
    assert "Possible duplicate" in page and "same REX" in page and "Save anyway" in page
    assert wg_orders.find_day(move_date) is None, "nothing saved"
    page = _post_day(client, move_date, stops, truck=_truck(), force_duplicate="on").get_data(as_text=True)
    assert "Saved the WG Day" in page, _flashes(page)
    # Validation: nothing saved, the typed stops stay on the page
    other = _free_day()
    page = _post_day(client, other, [_stop("No Address " + tag, "10:00", "09:00", address="")]).get_data(as_text=True)
    assert "Address is required." in page and "Finish time must be after start time." in page
    assert "No Address " + tag in page and wg_orders.find_day(other) is None
    page = _post_day(client, other, [{"customer": "", "payment_type": "Invoice"}]).get_data(as_text=True)
    assert "Add at least one stop." in page
    assert wg_orders.reference_numbers("REX 1649 AND REX 2359") == {"1649", "2359"}
    return True


def test_wg_outstanding_and_no_customer_messages():
    client = _client()
    past = _free_day(past=True)
    day, tag, _d, _t = _make_day(client, move_date=past)
    b = dict(db.get_booking(day["id"]))
    today_iso = perth_today().isoformat()
    assert dashboard_data.dashboard_section_group(b, today_iso) == dashboard_data.SECTION_WG_UNPAID
    summaries = dashboard_data.dashboard_unpaid_summaries([b], today_iso)
    assert summaries["wg"]["count"] == 1 and summaries["regular"]["count"] == 0
    booking = services.booking_to_dict(db.get_booking(day["id"]))
    assert payment_reminder_automation.is_eligible(booking) is False
    assert sms.send_booking_notification(booking, event="created") is None
    return True


def test_older_bookings_move_into_day():
    client = _client()
    move_date = _free_day()
    tag = _tag()
    _, crew_name = _crew("Yasu")
    # An older per-customer WG booking (the first WG Orders format: one booking per customer)
    old = db.create_booking("WG - " + TEST_PREFIX + "Old Order " + tag, "0400 123 456", "", "WG Outdoor Life",
                            "5 Old St, Perth WA", move_date, 1, "Old note", start_time="13:00", finish_time="15:00",
                            crew=crew_name, hourly_rate=0, status="Confirmed")
    db.save_booking_wg_fields(old, {"source": wg_jobs.WG_ORDER_SOURCE, "wg_products": "1 x Old Chair",
                                    "wg_reference": "REX 4242", "wg_payment_amount": 330, "wg_payment_type": "Confirmed"})
    # A hand-made "WG" day booking
    manual = db.create_booking("WG", "", "", "WG", "TBC", move_date, 2, "", start_time="09:00", finish_time="16:00",
                               hourly_rate=180, callout_fee=90, callout_minutes=30, status="Confirmed")
    page = client.get("/wg-orders?date=" + move_date).get_data(as_text=True)
    assert "Older WG bookings on this day" in page and "Move into WG Day" in page and "Use as this date’s WG Day" in page
    # Nothing changes without the confirm box
    client.post("/wg-orders/move/{0}".format(old), data={}, follow_redirects=True)
    assert db.get_booking(old) is not None
    # Use the hand-made booking as the WG Day: crew / times / invoice untouched
    client.post("/wg-orders/adopt/{0}".format(manual), follow_redirects=True)
    adopted = dict(db.get_booking(manual))
    assert wg_jobs.is_wg_day(adopted) and adopted["start_time"] == "09:00" and float(adopted["hourly_rate"]) == 180
    page = client.post("/wg-orders/move/{0}".format(old), data={"confirm": "yes"}, follow_redirects=True).get_data(as_text=True)
    assert "Moved #{0}".format(old) in page, _flashes(page)
    assert db.get_booking(old) is None
    stops = db.list_wg_stops(manual)
    assert len(stops) == 1 and stops[0]["customer"] == TEST_PREFIX + "Old Order " + tag
    assert stops[0]["reference"] == "REX 4242" and float(stops[0]["payment_amount"]) == 330 and stops[0]["products"] == "1 x Old Chair"
    assert dict(db.get_booking(manual))["start_time"] == "09:00", "adopted day keeps its hours"
    assert len(_wg_rows(move_date)) == 1
    # A paid older booking is left alone
    paid = db.create_booking("WG - Paid Order " + tag, "", "", "WG Outdoor Life", "6 Paid St", move_date, 1, "",
                             start_time="12:00", payment_status="Paid", status="Completed")
    db.save_booking_wg_fields(paid, {"source": wg_jobs.WG_ORDER_SOURCE, "wg_reference": "REX 5151"})
    page = client.post("/wg-orders/move/{0}".format(paid), data={"confirm": "yes"}, follow_redirects=True).get_data(as_text=True)
    assert "marked Paid" in page and db.get_booking(paid) is not None
    return True


def test_case_j_normal_bookings_unchanged():
    client = _client()
    move_date = _free_day()
    tag = _tag()
    normal = db.create_booking("Normal Customer " + tag, "0400111222", "n@example.com", "1 A St", "2 B St", move_date, 2, "",
                               hourly_rate=180, start_time="08:00", finish_time="10:00")
    b = services.booking_to_dict(db.get_booking(normal))
    assert not wg_jobs.is_wg_order(b) and not wg_jobs.is_wg_day(b)
    assert len(invoice.invoice_labour_lines(b)) == 1
    event = calendar_data.calendar_event(b)
    assert event["is_wg"] is False and event["is_wg_day"] is False
    assert wg_orders.staff_fields(b) == {"is_wg": False, "is_wg_day": False}
    assert "WG Orders" in client.get("/dashboard").get_data(as_text=True)
    cal = client.get(_day_url(move_date)).get_data(as_text=True)
    assert "Normal Customer " + tag in cal and "2 movers" in cal
    # Search finds the WG Day by a stop's customer
    day, day_tag, _d, _t = _make_day(client)
    assert day["id"] in [r["id"] for r in db.search_bookings("Paul Dever " + day_tag)]
    assert normal in [r["id"] for r in db.search_bookings("Normal Customer " + tag)]
    return True


def main():
    tests = [
        test_case_a_one_booking_dashboard_one_row,
        test_case_b_calendar_one_entry,
        test_case_c_crew_once_for_the_day,
        test_case_d_truck_once_one_day_per_date,
        test_case_e_staff_portal_one_job,
        test_case_f_run_sheet_all_stops_in_order,
        test_case_g_pdf_all_stops,
        test_case_h_map_and_call_each_stop,
        test_edit_resort_and_delete_stop,
        test_duplicates_and_validation,
        test_wg_outstanding_and_no_customer_messages,
        test_older_bookings_move_into_day,
        test_case_j_normal_bookings_unchanged,
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
