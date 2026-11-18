#!/usr/bin/env python3
"""E2E — WG Day form + photo / PDF import (wg_import.py → one WG Day with stops).

  A  one manual stop → one WG Day saved
  B  four manual stops → one WG Day booking with 4 stops
  C  ten stops → page and Save WG Day handle them (one booking)
  D  crew: set once for the day; one <label for> per person, checkbox + name together
  E  image with 4 orders → Analyse gives one WG Day: date, crew, 4 stops (Claude vision mocked)
  F  multi-page PDF with several orders per page → every stop read (text PDF, no AI)
  G  products keep their line breaks (import → form → stop)
  H  unclear fields → yellow warnings (time, address, phone, crew, payment, date)
  I  Analyse never creates a booking
  J  Save after Analyse → WG Day on Calendar / Staff Portal / WG Outstanding
Plus: upload checks (type, signature, size, executable), shared day heading → the day's
date, "YASU $320" → 320 + YASU, REX lists kept, duplicate warning on import.
"""

import io
import json
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

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from werkzeug.datastructures import MultiDict

import auth
import calendar_data
import dashboard_data
import database as db
import wg_import
import wg_jobs
import wg_orders
from app import app
from dashboard_data import perth_today

_n = 0
_rng = random.Random()
PRODUCTS = ["1 x Atlantis Skorpio 720", "1 x Atlantis Lotus 800", "1 x Landare Correa 760", "1 x Litestone Table Grove"]


def _tag():
    global _n
    _n += 1
    return "{0}-{1}".format(os.getpid(), _n)


def _client():
    db.init_db()
    label = "wgimp-{0}".format(_tag())
    uid = db.create_staff_user(label, auth.hash_password("t"), "WG Import", is_admin=1)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _crew(prefix):
    db.init_db()
    name = prefix + "".join(_rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(6))
    return db.create_crew_member(name, role="Mover", active=1), name


def _truck():
    name = "WGImpTruck-{0}".format(_tag())
    db.create_truck(name)
    return name


def _rex():
    return "REX {0}".format(_rng.randint(100000, 999999))


def _wg_day_rows(day_iso):
    return [dict(r) for r in db.list_by_date(day_iso) if wg_jobs.is_wg_day(dict(r))]


def _stops_named(prefix):
    with db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM wg_stops WHERE customer LIKE ?", (prefix + "%",)).fetchall()
    return [dict(r) for r in rows]


def _free_day(near=False):
    """A date without a WG Day yet; near = within a few weeks (headings without a year)."""
    while True:
        day = perth_today() + timedelta(days=_rng.randint(1, 25) if near else _rng.randint(400, 4000))
        if wg_orders.find_day(day.isoformat()) is None:
            return day


def _post_day(client, day_iso, stops, crew=(), truck="", **extra):
    payload = [("day", day_iso), ("move_date", day_iso), ("truck", truck)] + [("crew", c) for c in crew]
    payload += list(extra.items())
    for n, stop in enumerate(stops):
        payload += [("stops-{0}-{1}".format(n, k), v) for k, v in stop.items()]
    return client.post("/wg-orders", data=MultiDict(payload), follow_redirects=True)


def _manual(name, start, finish):
    return {"customer": name, "start_time": start, "finish_time": finish,
            "address": "{0} Test Rd, Duncraig".format(_rng.randint(1, 99999)), "phone": "0488 998 688",
            "payment_amount": "320", "payment_type": "COD", "reference": _rex(), "products": "1 x Chair", "notes": ""}


def _wg_pdf(pages):
    """A text PDF; pages = list of lists of lines."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    for lines in pages:
        y = 800
        for line in lines:
            pdf.drawString(40, y, line)
            y -= 14
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def _order_lines(label, name, time, address, phone, payment, ready, products=PRODUCTS, notes="Parking at front"):
    lines = [label, "Name: " + name, "Products:"] + list(products)
    lines += ["Date/Time: " + time, "Address: " + address, "Phone: " + phone, "Payment: " + payment, "Ready: " + ready, "Notes: " + notes, ""]
    return lines


def _upload(client, filename, data, day_iso=""):
    return client.post(
        "/wg-orders/import",
        data={"day": day_iso, "file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def _cards(html, imported_only=True):
    cls = r"wg-order wg-stop wg-order-imported" if imported_only else r"wg-order wg-stop[^\"]*"
    form = html.split('id="wg-stop-template"')[0]
    return re.findall(r'<fieldset class="{0}" data-wg-stop>(.*?)</fieldset>'.format(cls), form, re.S)


def _value(card, field):
    m = re.search(r'name="stops-\d+-{0}"[^>]*value="([^"]*)"'.format(field), card)
    if m:
        return m.group(1)
    m = re.search(r'name="stops-\d+-{0}"[^>]*>(.*?)</textarea>'.format(field), card, re.S)
    return m.group(1) if m else None


def _day_value(html, field):
    m = re.search(r'name="{0}" value="([^"]*)"'.format(field), html)
    return m.group(1) if m else None


def _checked_crew(html):
    return re.findall(r'name="crew" value="([^"]+)" checked', html)


STOP_FIELDS = ("customer", "start_time", "finish_time", "address", "phone", "payment_amount", "payment_type",
               "reference", "products", "notes")


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _mock_ai(orders, date_text="", unreadable=""):
    """Replace the Claude call with a canned answer; returns the list of captured requests."""
    captured = []

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode("utf-8")))
        text = json.dumps({"date_text": date_text, "orders": orders, "unreadable": unreadable})
        return _FakeResponse({"content": [{"type": "text", "text": "```json\n" + text + "\n```"}]})

    wg_import.urllib.request.urlopen = fake_urlopen
    os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
    return captured


def _unmock_ai():
    wg_import.urllib.request.urlopen = _REAL_URLOPEN
    os.environ.pop("ANTHROPIC_API_KEY", None)


import urllib.request as _ur  # noqa: E402

_REAL_URLOPEN = _ur.urlopen
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_case_a_one_manual_stop():
    client = _client()
    day = _free_day().isoformat()
    name = "ImpA " + _tag()
    resp = _post_day(client, day, [_manual(name, "10:00", "11:30")], truck=_truck())
    assert "Saved the WG Day" in resp.get_data(as_text=True)
    assert len(_wg_day_rows(day)) == 1 and len(_stops_named(name)) == 1
    return True


def test_case_b_four_manual_stops_one_booking():
    client = _client()
    day = _free_day().isoformat()
    tag = _tag()
    stops = [_manual("ImpB{0} {1}".format(i, tag), s, f)
             for i, (s, f) in enumerate([("10:00", "11:30"), ("11:30", "13:30"), ("12:00", "13:30"), ("13:00", "15:00")], 1)]
    resp = _post_day(client, day, stops, truck=_truck())
    assert "Saved the WG Day" in resp.get_data(as_text=True) and "4 stops" in resp.get_data(as_text=True)
    rows = _wg_day_rows(day)
    assert len(rows) == 1 and int(rows[0]["wg_stop_count"]) == 4
    assert [s["customer"] for s in db.list_wg_stops(rows[0]["id"])] == ["ImpB{0} {1}".format(i, tag) for i in range(1, 5)]
    return True


def test_case_c_ten_stops():
    client = _client()
    day = _free_day().isoformat()
    tag = _tag()
    stops = [_manual("ImpC{0:02d} {1}".format(i, tag), "{0:02d}:00".format(7 + i), "{0:02d}:30".format(7 + i)) for i in range(10)]
    resp = _post_day(client, day, stops, truck=_truck())
    assert "10 stops" in resp.get_data(as_text=True)
    rows = _wg_day_rows(day)
    assert len(rows) == 1
    assert [s["customer"] for s in db.list_wg_stops(rows[0]["id"])] == ["ImpC{0:02d} {1}".format(i, tag) for i in range(10)]
    page = client.get("/wg-orders?date=" + day).get_data(as_text=True)
    assert 'id="wg-stop-add"' in page and 'id="wg-stop-template"' in page and 'id="wg-stop-count"' in page
    assert len(_cards(page, imported_only=False)) == 10
    assert "Remove Order" not in page and "Copy previous" not in page
    return True


def test_case_d_crew_labels():
    client = _client()
    _crew("Katsu")
    html = client.get("/wg-orders?date=" + _free_day().isoformat()).get_data(as_text=True)
    options = re.findall(r'<label class="wg-crew-option" for="([^"]+)">\s*<input type="checkbox" id="([^"]+)" name="crew" value="([^"]+)"[^>]*>\s*<span>([^<]+)</span>', html)
    assert options, "crew options missing"
    for label_for, input_id, value, text in options:
        assert label_for == input_id and value == text
    assert html.count('data-wg-crew') == 1, "one crew block for the whole day"
    css = (ROOT / "static" / "style.css").read_text()
    assert '.wg-order-grid input:not([type="checkbox"])' in css
    assert re.search(r"\.wg-crew-option \{[^}]*flex-direction: row;[^}]*align-items: center;", css, re.S)
    return True


def test_case_e_image_four_orders_ai_mocked():
    client = _client()
    _, yasu = _crew("Yasu")
    day = _free_day(near=True)
    heading = day.strftime("%A ") + str(day.day) + "th " + day.strftime("%B")
    tag = _tag()
    rex = [_rex() for _ in range(4)]
    orders = [
        {"crew_hint": yasu, "customer": "Jacob Carmody " + tag, "products": "\n".join(PRODUCTS[:3]), "time": "10–11:30",
         "address": "15 Cassinia Rd, Duncraig", "phone": "0488 998 688", "payment": "YASU $320", "reference": rex[0], "notes": "Deliver, install and RR.", "uncertain": []},
        {"crew_hint": yasu, "customer": "Paul Dever " + tag, "products": "1 x Chair", "time": "11:30–1:30",
         "address": "7 Canning Hwy, East Fremantle", "phone": "0400 111 222", "payment": "$330 CONFIRMED", "reference": rex[1], "notes": "", "uncertain": []},
        {"crew_hint": yasu, "customer": "John Webb " + tag, "products": "1 x Table", "time": "12:00–1:30",
         "address": "3 Webb St, Perth", "phone": "0400 333 444", "payment": "$220 COD", "reference": rex[2] + " AND " + rex[3], "notes": "", "uncertain": []},
        {"crew_hint": yasu, "customer": "Jamie Dodd " + tag, "products": "1 x Bench", "time": "1:00–3:00",
         "address": "9 Dodd Ave, Wembley", "phone": "0400 555 666", "payment": "$440 COD", "reference": _rex(), "notes": "", "uncertain": []},
    ]
    captured = _mock_ai(orders, date_text=heading)
    try:
        before = len(db.list_by_date(day.isoformat()))
        resp = _upload(client, "wg-run.png", PNG)
    finally:
        _unmock_ai()
    html = resp.get_data(as_text=True)
    assert "4 stops found" in html, re.findall(r"stops? found[^<]*|No WG[^<]*", html)[:3]
    assert captured and captured[0]["messages"][0]["content"][0]["type"] == "image"
    assert captured[0]["messages"][0]["content"][0]["source"]["media_type"] == "image/png"
    # One WG Day: the heading's date, Yasu once for the day, 4 stops
    assert _day_value(html, "move_date") == day.isoformat(), "day heading → the WG Day's date"
    assert _checked_crew(html) == [yasu]
    cards = _cards(html)
    assert len(cards) == 4
    assert [_value(c, "customer") for c in cards] == [o["customer"] for o in orders]
    assert [(_value(c, "start_time"), _value(c, "finish_time")) for c in cards] == [
        ("10:00", "11:30"), ("11:30", "13:30"), ("12:00", "13:30"), ("13:00", "15:00")]
    assert [(_value(c, "payment_amount"), _value(c, "payment_type")) for c in cards] == [
        ("320", "YASU"), ("330", "Confirmed"), ("220", "COD"), ("440", "COD")]
    assert _value(cards[2], "reference") == rex[2] + " AND " + rex[3]
    assert "Time read as PM" in cards[3]
    assert len(db.list_by_date(day.isoformat())) == before, "Analyse never saves"
    return True


def test_case_f_multi_page_pdf_text():
    client = _client()
    _, yasu = _crew("Yasu")
    day = _free_day(near=True)
    heading = day.strftime("%A ") + str(day.day) + "th " + day.strftime("%B")
    tag = _tag()
    page1 = [heading, yasu.upper(), ""]
    page1 += _order_lines(yasu + " 1", "PdfOne " + tag, "10-11:30", "15 Cassinia Rd, Duncraig", "0488 998 688", "YASU $320", _rex())
    page1 += _order_lines(yasu + " 2", "PdfTwo " + tag, "11:30-1:30", "7 Canning Hwy, East Fremantle", "0400 111 222", "$330 CONFIRMED", _rex())
    page2 = _order_lines(yasu + " 3", "PdfThree " + tag, "12:00-1:30", "3 Webb St, Perth", "0400 333 444", "$220 COD", "REX 1649 AND REX 2359")
    page2 += _order_lines(yasu + " 4", "PdfFour " + tag, "1:00-3:00", "9 Dodd Ave, Wembley", "0400 555 666", "$440 COD", _rex())
    page3 = _order_lines(yasu + " 5", "PdfFive " + tag, "2-4", "21 Five St, Hillarys", "0400 777 888", "$100 COD", _rex())
    os.environ.pop("ANTHROPIC_API_KEY", None)
    resp = _upload(client, "WG orders.pdf", _wg_pdf([page1, page2, page3]))
    html = resp.get_data(as_text=True)
    assert "5 stops found" in html, re.findall(r"No WG[^<]*|stops? found[^<]*|not set up[^<]*", html)
    assert "3 pages" in html and "WG_orders.pdf" in html, "filename sanitised"
    cards = _cards(html)
    assert [_value(c, "customer") for c in cards] == ["Pdf{0} {1}".format(w, tag) for w in ("One", "Two", "Three", "Four", "Five")]
    assert _day_value(html, "move_date") == day.isoformat() and _checked_crew(html) == [yasu]
    assert _value(cards[2], "reference") == "REX 1649 AND REX 2359"
    assert _value(cards[0], "products") == "\n".join(PRODUCTS)
    return True


def _form_stops(html):
    return [{k: _value(c, k) or "" for k in STOP_FIELDS} for c in _cards(html, imported_only=False)]


def test_case_g_products_line_breaks_through_save():
    client = _client()
    day = _free_day()
    tag = _tag()
    lines = _order_lines("Order 1", "Lines " + tag, "9-10", "5 Line St, Perth", "0400 000 111", "$50 COD", _rex())
    lines = [day.strftime("%A ") + str(day.day) + " " + day.strftime("%B %Y")] + lines
    html = _upload(client, "lines.pdf", _wg_pdf([lines])).get_data(as_text=True)
    assert _value(_cards(html)[0], "products") == "\n".join(PRODUCTS)
    resp = _post_day(client, _day_value(html, "move_date"), _form_stops(html), truck=_truck())
    assert "Saved the WG Day" in resp.get_data(as_text=True)
    saved = _stops_named("Lines " + tag)[0]
    assert saved["products"] == "\n".join(PRODUCTS) and saved["payment_type"] == "COD"
    return True


def test_case_h_unclear_fields_warn():
    client = _client()
    orders = [{"crew_hint": "Someone", "customer": "Blurry " + _tag(), "products": "1 x ???", "time": "about lunch",
               "address": "Duncraig", "phone": "0488 99", "payment": "COD", "reference": "", "notes": "",
               "uncertain": ["address", "products"]}]
    day_iso = _free_day().isoformat()
    _mock_ai(orders, date_text="")
    try:
        html = _upload(client, "photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 64, day_iso=day_iso).get_data(as_text=True)
    finally:
        _unmock_ai()
    card = _cards(html)[0]
    for msg in ("Time unclear", "Address may need review", "Phone number uncertain", "Payment unclear", "Products may need review"):
        assert msg in card, msg
    head = html.split('id="wg-stop-rows"')[0]
    assert "Crew “Someone” not found" in head and "Date unclear" in head
    assert card.count("wg-field-warn") >= 4
    assert _value(card, "start_time") == "" and _day_value(html, "move_date") == day_iso
    assert "need checking (yellow)" in html
    return True


def test_case_i_analyse_does_not_create_bookings():
    client = _client()
    tag = _tag()
    before = len(_stops_named("NoSave"))
    pdf = _wg_pdf([_order_lines("Order 1", "NoSave " + tag, "9-10", "5 Save St, Perth", "0400 000 111", "$50 COD", _rex())])
    html = _upload(client, "nosave.pdf", pdf, day_iso=_free_day().isoformat()).get_data(as_text=True)
    assert "NoSave " + tag in html and "Not saved yet" in html
    assert len(_stops_named("NoSave")) == before
    return True


def test_case_j_save_after_import_everywhere():
    client = _client()
    crew_id, yasu = _crew("Yasu")
    day = _free_day()
    tag = _tag()
    truck = _truck()
    pdf = _wg_pdf([[day.strftime("%A ") + str(day.day) + " " + day.strftime("%B %Y"), yasu.upper()]
                   + _order_lines(yasu + " 1", "Flow " + tag, "8-9", "{0} Flow St, Perth".format(_rng.randint(1, 99999)), "0400 000 111", "YASU $320", _rex())])
    html = _upload(client, "flow.pdf", pdf).get_data(as_text=True)
    resp = _post_day(client, _day_value(html, "move_date"), _form_stops(html), crew=_checked_crew(html), truck=truck)
    assert "Saved the WG Day" in resp.get_data(as_text=True)
    saved = _wg_day_rows(day.isoformat())[0]
    stop = db.list_wg_stops(saved["id"])[0]
    assert stop["payment_type"] == "YASU" and float(stop["payment_amount"]) == 320 and saved["crew"] == yasu
    assert calendar_data.calendar_event(saved)["is_wg_day"]
    url = "/staff?staff_id={0}&range=calendar&cal_view=month&year={1}&month={2}".format(crew_id, day.year, day.month)
    staff = app.test_client().get(url).get_data(as_text=True)
    assert '/staff/wg/{0}"'.format(saved["id"]) in staff and "Flow " + tag not in staff
    sheet = app.test_client().get("/staff/wg/{0}".format(saved["id"])).get_data(as_text=True)
    assert "Flow " + tag in sheet and "$320 · YASU" in sheet
    # WG Outstanding: once the date has passed and it is unpaid → WG section, not REGULAR UNPAID
    after = (day + timedelta(days=1)).isoformat()
    assert dashboard_data.dashboard_section_group(saved, after) == dashboard_data.SECTION_WG_UNPAID
    return True


def test_upload_checks_and_duplicates():
    client = _client()
    bad = [
        ("run.exe", b"MZ\x90\x00" + b"\x00" * 32, "Only JPG, JPEG, PNG or PDF"),
        ("fake.png", b"%PDF-1.4 not a png", "doesn't match its type"),
        ("empty.pdf", b"", "The file is empty"),
        ("script.pdf.sh", b"#!/bin/sh", "Only JPG, JPEG, PNG or PDF"),
    ]
    for name, data, msg in bad:
        import html as html_mod

        page = html_mod.unescape(_upload(client, name, data).get_data(as_text=True))
        assert msg in page, (name, msg)
    try:
        wg_import.check_upload("big.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * (wg_import.MAX_UPLOAD_BYTES + 1))
        raise AssertionError("size limit not enforced")
    except wg_import.ImportError_ as exc:
        assert "too large" in str(exc)
    # Image without an API key → clear message, nothing saved
    os.environ.pop("ANTHROPIC_API_KEY", None)
    html = _upload(client, "photo.png", PNG).get_data(as_text=True)
    assert "not set up yet" in html
    # A stop already on that date's WG Day → the imported copy is flagged; saved stops come first
    future = _free_day()
    day = future.isoformat()
    rex = _rex()
    existing = _manual("DupImp " + _tag(), "10:00", "11:00")
    existing["reference"] = rex
    _post_day(client, day, [existing], truck=_truck())
    heading = future.strftime("%A ") + str(future.day) + future.strftime(" %B %Y")
    pdf = _wg_pdf([[heading] + _order_lines("Order 1", "Someone Else " + _tag(), "12-1", "1 Other St, Perth", "0400 000 111", "$50 COD", rex)])
    html = _upload(client, "dup.pdf", pdf).get_data(as_text=True)
    assert "Possible duplicate" in html and "same REX" in html and "added after the 1 saved stop" in html
    assert len(_cards(html, imported_only=False)) == 2 and _value(_cards(html, imported_only=False)[0], "customer") == existing["customer"]
    return True


def main():
    tests = [
        test_case_a_one_manual_stop,
        test_case_b_four_manual_stops_one_booking,
        test_case_c_ten_stops,
        test_case_d_crew_labels,
        test_case_e_image_four_orders_ai_mocked,
        test_case_f_multi_page_pdf_text,
        test_case_g_products_line_breaks_through_save,
        test_case_h_unclear_fields_warn,
        test_case_i_analyse_does_not_create_bookings,
        test_case_j_save_after_import_everywhere,
        test_upload_checks_and_duplicates,
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
        finally:
            wg_import.urllib.request.urlopen = _REAL_URLOPEN
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
