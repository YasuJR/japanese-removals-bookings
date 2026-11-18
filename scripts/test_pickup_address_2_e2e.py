#!/usr/bin/env python3
"""E2E — optional second pickup address (bookings.pickup_address_2, pickups.py).

  A  Pickup 1 + Delivery → saved and shown exactly as before (no "Pickup 2" anywhere)
  B  Pickup 1 + Pickup 2 + Delivery → saved, re-shown on Edit, Booking Detail in order
  C  Pickup 2 added then removed → cleared on save
  D  Staff Portal → both pickups in order, Map Pickup 1 / Map Pickup 2 links
  E  Invoice / Receipt → "Pickup:" with numbered addresses (preview + PDF)
  F  Job Sheet PDF (+ Staff Weekly PDF, Run Sheet) → Pickup 1 / Pickup 2 / Delivery
  G  Search → found by the Pickup 2 address
  H  existing booking (no Pickup 2) → forms without the section never touch it
Plus: Analyse (Paste Customer Information) recognises clear two-pickup text and leaves
unclear text in Notes, Google Calendar description, CSV export.
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")
for _flag in ("GOOGLE_CALENDAR_ENABLED", "GMAIL_INBOX_ENABLED", "XERO_ENABLED", "SMS_ENABLED", "STRIPE_ENABLED", "EMAIL_ENABLED"):
    os.environ.setdefault(_flag, "false")

import pymupdf

import auth
import booking_helpers
import database as db
import invoice
import services
from crew import display_crew
from app import app
from dashboard_data import perth_today
from integrations import enquiry_parser
from integrations import invoice_pdf
from integrations import job_sheet
from integrations import weekly_schedule_pdf
from staff_portal import build_staff_weekly_pdf_schedule

P1 = "52 Kilkenny Street, Beckenham WA"
P2 = "3/132 Ardross Street, Applecross WA"
DELIVERY = "43 Ogilvie Street, Mount Hawthorn WA"
_n = 0


def _tag():
    global _n
    _n += 1
    return "{0}-{1}".format(os.getpid(), _n)


def _client():
    db.init_db()
    label = "pickup2-{0}".format(_tag())
    uid = db.create_staff_user(label, auth.hash_password("t"), "Pickup Test", is_admin=1)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _crew():
    db.init_db()
    name = "PickCrew-{0}".format(_tag())
    return db.create_crew_member(name, role="Mover", active=1), name


def _new_form(name, crew_name, **overrides):
    form = {
        "customer_name": name,
        "phone": "0400111222",
        "email": "pickup2@example.com",
        "pickup_address": P1,
        "delivery_address": DELIVERY,
        "move_date": perth_today().isoformat(),
        "num_movers": "2",
        "notes": "",
        "start_time": "08:00",
        "finish_time": "11:00",
        "duration_hours": "",
        "hourly_rate": "180",
        "callout_minutes": "0",
        "gst_enabled": "on",
        "status": "Confirmed",
        "crew": crew_name,
        "pickup_address_2_enabled": "0",
        "pickup_address_2": "",
        "action": "save",
        "double_booking_override_confirm": "on",
    }
    form.update(overrides)
    return form


def _create(client, crew_name, **overrides):
    name = "Pickup Two {0}".format(_tag())
    resp = client.post("/bookings/new", data=_new_form(name, crew_name, **overrides), follow_redirects=True)
    html = resp.get_data(as_text=True)
    match = re.search(r"reference #(\d+)", html)
    assert match, html[:2000]
    return int(match.group(1)), name


def _edit_form(booking_id, **overrides):
    row = dict(db.get_booking(booking_id))
    form = _new_form(row["customer_name"], row["crew"] or "")
    form.update({"payment_status": row["payment_status"] or "Unpaid", "invoice_status": row["invoice_status"] or ""})
    form["pickup_address_2_enabled"] = "1" if row.get("pickup_address_2") else "0"
    form["pickup_address_2"] = row.get("pickup_address_2") or ""
    form.update(overrides)
    return form


def _row(booking_id):
    return dict(db.get_booking(booking_id))


def _pdf_text(data):
    doc = pymupdf.open(stream=data, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def _squash(text):
    return re.sub(r"\s+", " ", text)


def test_case_a_single_pickup_unchanged():
    client = _client()
    _, crew_name = _crew()
    bid, _ = _create(client, crew_name)
    row = _row(bid)
    assert row["pickup_address"] == P1 and not row["pickup_address_2"]
    detail = client.get("/bookings/{0}".format(bid)).get_data(as_text=True)
    assert "<dt>Pickup address</dt>" in detail and "Pickup 2" not in detail
    edit = client.get("/bookings/{0}/edit".format(bid)).get_data(as_text=True)
    assert 'id="pickup-2-box" hidden' in edit and "Add another pickup address" in edit
    assert "Pickup 2 Map" not in edit
    booking = services.booking_to_dict(db.get_booking(bid))
    assert invoice.invoice_customer_bill_to(booking)["pickup_lines"] == ["Pickup: " + P1]
    assert "Pickup 2" not in _pdf_text(job_sheet.generate_job_sheet_pdf(booking))
    assert "Pickup 1" not in booking_helpers.build_calendar_description(booking, display_crew=display_crew)
    return True


def test_case_b_two_pickups_saved_and_shown_in_order():
    client = _client()
    _, crew_name = _crew()
    bid, _ = _create(client, crew_name, pickup_address_2_enabled="1", pickup_address_2=P2)
    assert _row(bid)["pickup_address_2"] == P2
    edit = client.get("/bookings/{0}/edit".format(bid)).get_data(as_text=True)
    assert 'value="{0}"'.format(P2) in edit and 'id="pickup-2-box" hidden' not in edit
    assert "Pickup 1 Map" in edit and "Pickup 2 Map" in edit
    detail = client.get("/bookings/{0}".format(bid)).get_data(as_text=True)
    i1, i2, i3 = detail.index("<dt>Pickup 1</dt>"), detail.index("<dt>Pickup 2</dt>"), detail.index("<dt>Delivery</dt>")
    assert i1 < i2 < i3 and P2 in detail
    # Editing other fields keeps Pickup 2
    client.post("/bookings/{0}/edit".format(bid), data=_edit_form(bid, notes="changed"))
    assert _row(bid)["pickup_address_2"] == P2 and _row(bid)["notes"] == "changed"
    return True


def test_case_c_remove_second_pickup_clears_it():
    client = _client()
    _, crew_name = _crew()
    bid, _ = _create(client, crew_name, pickup_address_2_enabled="1", pickup_address_2=P2)
    # "Remove second pickup" sets the flag to 0 (and empties the field in the browser)
    client.post("/bookings/{0}/edit".format(bid), data=_edit_form(bid, pickup_address_2_enabled="0"))
    assert not _row(bid)["pickup_address_2"]
    # Section open but left blank also saves as no Pickup 2
    client.post("/bookings/{0}/edit".format(bid), data=_edit_form(bid, pickup_address_2_enabled="1", pickup_address_2="  "))
    assert not _row(bid)["pickup_address_2"]
    detail = client.get("/bookings/{0}".format(bid)).get_data(as_text=True)
    assert "Pickup 2" not in detail
    return True


def test_case_d_staff_portal_addresses_and_maps():
    client = _client()
    crew_id, crew_name = _crew()
    bid, name = _create(client, crew_name, pickup_address_2_enabled="1", pickup_address_2=P2)
    map1, map2 = booking_helpers.apple_maps_url(P1), booking_helpers.apple_maps_url(P2)
    assert map1 and map2 and map1 != map2
    for url in (
        "/staff?staff_id={0}&range=today".format(crew_id),
        "/staff?staff_id={0}&range=week".format(crew_id),
        "/staff?staff_id=all&range=today",
        "/staff?staff_id=all&range=week",
        "/staff?staff_id={0}&range=calendar&cal_view=week&week=0".format(crew_id),
    ):
        html = app.test_client().get(url).get_data(as_text=True)
        assert name in html, url
        card = html[html.index(name):]
        card = card[: card.index(DELIVERY.split(",")[0]) + 400]
        assert P1 in card and P2 in card, url
        assert card.index(P1) < card.index(P2), url
        assert "Pickup 2" in card, url
        has_map1 = map1.replace("&", "&amp;") in html or map1 in html
        has_map2 = map2.replace("&", "&amp;") in html or map2 in html
        assert has_map1 == has_map2, url  # Pickup 2 is a map link wherever Pickup 1 is
    today = app.test_client().get("/staff?staff_id={0}&range=today".format(crew_id)).get_data(as_text=True)
    assert "Map Pickup 1" in today and "Map Pickup 2" in today and "Map Delivery" in today
    assert map1.replace("&", "&amp;") in today or map1 in today
    week_cal = app.test_client().get("/staff?staff_id={0}&range=calendar&cal_view=week&week=0".format(crew_id)).get_data(as_text=True)
    assert "2 pickups" in week_cal
    return True


def test_case_e_invoice_and_receipt_numbered_pickups():
    client = _client()
    _, crew_name = _crew()
    bid, _ = _create(client, crew_name, pickup_address_2_enabled="1", pickup_address_2=P2)
    preview = client.get("/bookings/{0}/invoice/preview".format(bid)).get_data(as_text=True)
    assert "Pickup:</p>" in preview and "1. " + P1 in preview and "2. " + P2 in preview
    assert preview.index("1. " + P1) < preview.index("2. " + P2) < preview.index("Drop-off: " + DELIVERY)
    booking = services.booking_to_dict(db.get_booking(bid))
    text = _squash(_pdf_text(invoice_pdf.generate_invoice_pdf(booking)))
    assert "1. " + P1 in text and "2. " + P2 in text and "Drop-off: " + DELIVERY in text
    client.post("/bookings/{0}/edit".format(bid), data={"action": "mark_paid", "payment_method": "Bank Transfer"})
    paid_on = _row(bid)["paid_at"][:10]
    client.post("/bookings/{0}/receipt".format(bid), data={"payment_method": "Bank Transfer", "payment_date": paid_on})
    rct = client.get("/bookings/{0}/receipt/preview".format(bid))
    assert rct.status_code == 200
    rct_html = rct.get_data(as_text=True)
    assert "1. " + P1 in rct_html and "2. " + P2 in rct_html
    rct_pdf = client.get("/bookings/{0}/receipt.pdf".format(bid))
    assert rct_pdf.status_code == 200
    text = _squash(_pdf_text(rct_pdf.data))
    assert "1. " + P1 in text and "2. " + P2 in text
    return True


def test_case_f_job_sheet_and_staff_pdfs():
    client = _client()
    crew_id, crew_name = _crew()
    long_p2 = "Unit 12/3 Very Long Street Name Boulevard Extension, Applecross Heights Western Australia 6153"
    bid, name = _create(client, crew_name, pickup_address_2_enabled="1", pickup_address_2=long_p2)
    booking = services.booking_to_dict(db.get_booking(bid))
    pdf = job_sheet.generate_job_sheet_pdf(booking)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = _squash(_pdf_text(pdf))
    assert text.index("Pickup 1") < text.index("Pickup 2") < text.index("Delivery")
    assert P1 in text and long_p2 in text and DELIVERY in text
    page = doc[0]
    for block in page.get_text("blocks"):
        assert block[2] <= page.rect.width - 10, "text runs off the page"
    res = client.get("/bookings/{0}/job-sheet.pdf".format(bid))
    assert res.status_code == 200
    schedule = build_staff_weekly_pdf_schedule(str(crew_id), 0, perth_today())
    weekly = _squash(_pdf_text(weekly_schedule_pdf.render_staff_weekly_schedule_pdf(schedule)))
    assert "P 1:" in weekly and "P 2:" in weekly
    all_schedule = build_staff_weekly_pdf_schedule("all", 0, perth_today())
    all_text = _squash(_pdf_text(weekly_schedule_pdf.render_staff_weekly_schedule_pdf(all_schedule)))
    assert "P 2:" in all_text
    return True


def test_case_g_search_by_second_pickup():
    client = _client()
    _, crew_name = _crew()
    unique = "{0} Zzyzx Lane, Bicton WA".format(_n + 700)
    bid, _ = _create(client, crew_name, pickup_address_2_enabled="1", pickup_address_2=unique)
    hits = [int(r["id"]) for r in db.search_bookings("Zzyzx")]
    assert bid in hits
    html = client.get("/bookings/search?q=Zzyzx").get_data(as_text=True)
    assert "/bookings/{0}".format(bid) in html
    return True


def test_case_h_existing_booking_untouched():
    db.init_db()
    _, crew_name = _crew()
    bid = db.create_booking(
        "Legacy Pickup {0}".format(_tag()), "0400111222", "legacy@example.com", P1, DELIVERY,
        perth_today().isoformat(), 2, "", crew=crew_name, status="Confirmed",
    )
    row = _row(bid)
    assert not row.get("pickup_address_2")
    booking = services.booking_to_dict(db.get_booking(bid))
    bill = invoice.invoice_customer_bill_to(booking)
    assert bill["pickup_line"] == "Pickup: " + P1 and bill["pickup_lines"] == [bill["pickup_line"]]
    # A form without the Pickup 2 section (e.g. older forms) never clears a saved Pickup 2
    db.save_booking_pickup_address_2(bid, P2)
    client = _client()
    form = _edit_form(bid)
    form.pop("pickup_address_2_enabled")
    form.pop("pickup_address_2")
    client.post("/bookings/{0}/edit".format(bid), data=form)
    assert _row(bid)["pickup_address_2"] == P2
    # CSV export keeps the old columns first, Pickup 2 appended at the end
    csv_text = client.get("/bookings/export.csv").get_data(as_text=True)
    header = csv_text.splitlines()[0] if csv_text else ""
    if header:
        assert header.startswith("id,customer_name,phone,email,pickup_address,delivery_address")
        assert header.endswith("pickup_address_2")
    return True


def test_analyse_recognises_two_pickups():
    labelled = enquiry_parser.parse_pasted_text(
        "John Smith 0412 345 678\nPickup 1:\n{0}\nPickup 2:\n{1}\nDelivery:\n{2}".format(P1, P2, DELIVERY)
    )
    assert (labelled["pickup_address"], labelled["pickup_address_2"], labelled["delivery_address"]) == (P1, P2, DELIVERY)
    assert P2 not in labelled["notes"]
    houses = enquiry_parser.parse_pasted_text(
        "Mary 0400 111 222\nHouse 1: 10 Stock Rd, Attadale\nHouse 2: 7 Canning Hwy, East Fremantle\nGoing to: 12 High St, Fremantle"
    )
    assert houses["pickup_address"] == "10 Stock Rd, Attadale"
    assert houses["pickup_address_2"] == "7 Canning Hwy, East Fremantle"
    assert houses["delivery_address"] == "12 High St, Fremantle"
    unclear = enquiry_parser.parse_pasted_text(
        "Pickup: 10 Stock Rd, Attadale\nDelivery: 12 High St, Fremantle\nalso a second pickup at my mum's place in Bicton"
    )
    assert unclear["pickup_address_2"] == "" and "second pickup" in unclear["notes"]
    assert any("second pickup" in w for w in unclear["warnings"])
    single = enquiry_parser.parse_pasted_text("Pickup: 10 Stock Rd, Attadale\nDelivery: 12 High St, Fremantle")
    assert single["pickup_address_2"] == "" and not any("second pickup" in w for w in single["warnings"])
    # Analyse → New Booking form opens Pickup 2 with the address filled in
    client = _client()
    html = client.post(
        "/bookings/new",
        data={"action": "analyse_paste", "paste_text": "Pickup 1: {0}\nPickup 2: {1}\nDelivery: {2}".format(P1, P2, DELIVERY)},
    ).get_data(as_text=True)
    assert 'value="{0}"'.format(P2) in html and 'id="pickup_address_2_enabled" value="1"' in html
    assert "From 2" in html
    return True


def test_calendar_description_lists_both_pickups():
    booking = {"id": 1, "customer_name": "Cal", "pickup_address": P1, "pickup_address_2": P2, "delivery_address": DELIVERY,
               "hourly_rate": 180, "callout_fee": 0, "gst_enabled": 1, "extra_charges": []}
    text = booking_helpers.build_calendar_description(booking, display_crew=display_crew)
    assert text.index("Pickup 1: " + P1) < text.index("Pickup 2: " + P2) < text.index("Delivery: " + DELIVERY)
    assert "Pickup 2 map" in text
    return True


def main():
    tests = [
        test_case_a_single_pickup_unchanged,
        test_case_b_two_pickups_saved_and_shown_in_order,
        test_case_c_remove_second_pickup_clears_it,
        test_case_d_staff_portal_addresses_and_maps,
        test_case_e_invoice_and_receipt_numbered_pickups,
        test_case_f_job_sheet_and_staff_pdfs,
        test_case_g_search_by_second_pickup,
        test_case_h_existing_booking_untouched,
        test_analyse_recognises_two_pickups,
        test_calendar_description_lists_both_pickups,
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
