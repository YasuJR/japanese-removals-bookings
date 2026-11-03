#!/usr/bin/env python3
"""E2E — customer furniture lists shown (read only) on Staff Portal job cards.

  A  list submitted → card summary (count, Beds/Sofas/Fridges/TVs) + View Furniture List panel
  B  no list / empty list → "Furniture List: Not submitted", no error
  C  50+ items → card stays short (no item lines), panel lists everything
  D  list updated after first submission → latest version, changed lines yellow
  E  Individual Staff (Today / Weekly / Calendar) → their job's furniture
  F  All Staff (Today / Weekly) → that booking's furniture
  G  iPhone layout → see scratchpad Playwright check (card/panel markup asserted here)
  H  Furniture PDF link opens (inline PDF, rendered only when tapped)
Plus: large items, furniture note separate from job note, read only, one query for all cards.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")
for _flag in ("GOOGLE_CALENDAR_ENABLED", "GMAIL_INBOX_ENABLED", "XERO_ENABLED", "SMS_ENABLED", "STRIPE_ENABLED", "EMAIL_ENABLED"):
    os.environ.setdefault(_flag, "false")

import auth
import database as db
import furniture_catalog as catalog
import furniture_lists
from app import app
from dashboard_data import perth_today

_n = 0


def _tag():
    global _n
    _n += 1
    return "{0}-{1}".format(os.getpid(), _n)


def _crew():
    db.init_db()
    name = "FurnCrew-{0}".format(_tag())
    return db.create_crew_member(name, role="Mover", active=1), name


def _booking(crew_name, move_date=None, notes="Job note: gate code 1234"):
    db.init_db()
    return db.create_booking(
        "Furn Staff {0}".format(_tag()), "0400111222", "staff-furn@example.com", "1 Test St, Perth",
        "2 Test Ave, Fremantle", move_date or perth_today().isoformat(), 2, notes, crew=crew_name,
        status="Confirmed",
    )


def _content():
    return {
        "rooms": [
            {"id": "r1", "type": "bedroom", "label": "Bedroom 1",
             "items": {"bed_queen": 1, "bed_single": 2, "bedside_table": 2, "mattress_queen": 1}},
            {"id": "r2", "type": "living", "label": "Living room",
             "items": {"sofa_3": 1, "sofa_2": 1, "tv_large": 1, "tv_small": 1, "tv_unit": 1, "piano_upright": 1}},
            {"id": "r3", "type": "kitchen", "label": "Kitchen",
             "items": {"fridge_large": 1, "dining_table_6": 1, "dining_chair": 6}},
            {"id": "r4", "type": "outdoor", "label": "Outdoor / garage", "items": {"pot_plant_small": 8}},
        ],
        "custom_items": [{"name": "Aquarium", "qty": 1, "length_cm": 150, "width_cm": 60, "height_cm": 120}],
        "notes": "Second floor\nBed needs dismantling\nCustomer will remove TV from wall",
    }


def _submit(booking_id, content):
    row = furniture_lists.ensure_for_booking(booking_id)
    customer = app.test_client()
    res = customer.post("/furniture/{0}/save".format(row["access_token"]), json={"content": content, "submit": True})
    assert res.status_code == 200, res.get_data(as_text=True)
    return furniture_lists.get_for_booking(booking_id)


def _card(html, booking_name):
    """The furniture section of the card for one booking."""
    start = html.index(booking_name)
    section = html[html.index('<section class="staff-furniture', start):]
    return section[: section.index("</section>") + len("</section>")]


def _name(booking_id):
    return dict(db.get_booking(booking_id))["customer_name"]


def _panel(card_html):
    import re

    url = re.search(r'data-panel-url="([^"]+)"', card_html).group(1)
    res = app.test_client().get(url.replace("&amp;", "&"))
    assert res.status_code == 200 and "no-store" in res.headers.get("Cache-Control", "")
    return res.get_data(as_text=True)


def test_case_a_summary_and_detail():
    crew_id, crew = _crew()
    b = _booking(crew)
    _submit(b, _content())
    html = app.test_client().get("/staff?staff_id={0}&range=today".format(crew_id)).get_data(as_text=True)
    card = _card(html, _name(b))
    assert "Furniture" in card and "29 items" in card, card
    for chip in ("Beds: <strong>3</strong>", "Mattresses: <strong>1</strong>", "Sofas: <strong>2</strong>",
                 "Fridges: <strong>1</strong>", "TVs: <strong>2</strong>"):
        assert chip in card, chip
    assert "View Furniture List" in card and "Has furniture note" in card
    assert "Queen bed + mattress" not in card, "item lines are loaded on tap, not in the card"
    panel = _panel(card)
    for line in ("Queen bed + mattress", "× 1", "Dining chair", "× 6", "Pot plant (small)", "× 8", "Aquarium"):
        assert line in panel, line
    assert "Bedroom 1" in panel and "Living room" in panel and "Kitchen" in panel and "Outdoor / garage" in panel
    # Large items: only from stored m³ per item / catalogue "Heavy item" hint.
    large = panel[panel.index("Large items"): panel.index("Furniture list")]
    for item in ("Queen bed + mattress", "3-seater sofa", "Fridge (French door / side-by-side)", "Dining table (6 seats)",
                 "Upright piano", "Aquarium"):
        assert item in large, item
    for small in ("Dining chair", "Pot plant (small)", "Bedside table", "TV up to 55"):
        assert small not in large, small
    # Furniture note is its own block, not the job note.
    assert "Furniture note" in panel and "Bed needs dismantling" in panel
    assert "gate code 1234" not in panel
    assert "gate code 1234" in html  # job note still on the card, separately
    return True


def test_case_b_not_submitted():
    crew_id, crew = _crew()
    none = _booking(crew)
    empty = _booking(crew)
    furniture_lists.ensure_for_booking(empty)  # created but never filled
    res = app.test_client().get("/staff?staff_id={0}&range=today".format(crew_id))
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    for b in (none, empty):
        card = _card(html, _name(b))
        assert "Furniture List:</span> Not submitted" in card and "View Furniture List" not in card
    return True


def test_case_c_fifty_plus_items():
    crew_id, crew = _crew()
    b = _booking(crew)
    keys = list(catalog.ITEMS.keys())
    rooms = []
    for i in range(0, 60, 15):
        rooms.append({"id": "r{0}".format(i), "type": "living", "label": "Room {0}".format(i),
                      "items": {k: 1 + (j % 3) for j, k in enumerate(keys[i:i + 15])}})
    content = {"rooms": rooms, "notes": "",
               "custom_items": [{"name": "Very-long-item-name-without-spaces-" * 3 + str(i), "qty": 1} for i in range(20)]}
    row = _submit(b, content)
    total = catalog.summarize(row["content"])["item_count"]
    assert total >= 50
    html = app.test_client().get("/staff?staff_id={0}&range=week".format(crew_id)).get_data(as_text=True)
    card = _card(html, _name(b))
    assert "{0} items".format(total) in card and card.count("<li>") <= len(furniture_lists.CARD_CATEGORIES)
    panel = _panel(card)
    assert panel.count("staff-furniture-qty") >= 80
    return True


def test_case_d_updated_list_shows_latest_and_yellow():
    crew_id, crew = _crew()
    b = _booking(crew)
    first = _content()
    _submit(b, first)
    second = _content()
    second["rooms"][0]["items"]["bed_queen"] = 2
    second["rooms"][2]["items"]["washing_machine"] = 1
    del second["rooms"][3]["items"]["pot_plant_small"]
    second["notes"] = "Second floor\nLarge pot plants"
    _submit(b, second)
    html = app.test_client().get("/staff?staff_id={0}&range=today".format(crew_id)).get_data(as_text=True)
    card = _card(html, _name(b))
    assert "Updated" in card and "Washers / dryers: <strong>1</strong>" in card
    panel = _panel(card)
    assert "Large pot plants" in panel and "Bed needs dismantling" not in panel, "latest notes only"
    assert 'class="staff-furniture-hl"' in panel and "was × 1" in panel and "added" in panel
    assert "Removed since first submission" in panel and "Pot plant (small)" in panel
    assert "staff-furniture-note staff-furniture-hl" in panel
    return True


def test_case_e_individual_staff_views():
    crew_id, crew = _crew()
    _other_id, other = _crew()
    today = perth_today()
    mine = _booking(crew, move_date=today.isoformat())
    theirs = _booking(other, move_date=today.isoformat())
    _submit(mine, _content())
    _submit(theirs, _content())
    client = app.test_client()
    urls = [
        "/staff?staff_id={0}&range=today",
        "/staff?staff_id={0}&range=week",
        "/staff?staff_id={0}&range=calendar&cal_view=week&week=0",
        "/staff?staff_id={0}&range=calendar&year={1}&month={2}&day={3}".format("{0}", today.year, today.month, today.day),
    ]
    for url in urls:
        html = client.get(url.format(crew_id)).get_data(as_text=True)
        assert _name(mine) in html and _name(theirs) not in html, url
        assert "View Furniture List" in _card(html, _name(mine)), url
    return True


def test_case_f_all_staff_views():
    crew_id, crew = _crew()
    b = _booking(crew)
    _submit(b, _content())
    client = app.test_client()
    for url in ("/staff?staff_id=all&range=today", "/staff?staff_id=all&range=week"):
        html = client.get(url).get_data(as_text=True)
        card = _card(html, _name(b))
        assert "29 items" in card and "View Furniture List" in card, url
        assert "Queen bed + mattress" in _panel(card)
    # Office weekly schedule (same partial) is unchanged: no furniture block.
    office = app.test_client()
    uid = db.create_staff_user("furn-office-{0}".format(_tag()), auth.hash_password("t"), "Office", is_admin=0)
    with office.session_transaction() as sess:
        sess["user_id"] = uid
    res = office.get("/calendar/weekly")
    assert res.status_code == 200
    office_html = res.get_data(as_text=True)
    assert _name(b) in office_html and "staff-furniture" not in office_html
    return True


def test_case_g_mobile_markup():
    crew_id, crew = _crew()
    b = _booking(crew)
    _submit(b, _content())
    html = app.test_client().get("/staff?staff_id={0}&range=today".format(crew_id)).get_data(as_text=True)
    assert "staff_furniture.js" in html and 'name="viewport"' in html
    card = _card(html, _name(b))
    assert '<details class="staff-furniture-details"' in card and "staff-job-btn staff-furniture-toggle" in card
    css = (ROOT / "static" / "staff_portal.css").read_text()
    assert ".staff-furniture-name { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }" in css
    return True


def test_case_h_pdf_opens():
    crew_id, crew = _crew()
    b = _booking(crew)
    row = _submit(b, _content())
    html = app.test_client().get("/staff?staff_id={0}&range=today".format(crew_id)).get_data(as_text=True)
    card = _card(html, _name(b))
    assert "View Furniture PDF" in card and 'target="_blank"' in card
    token = furniture_lists.staff_link_token(b)
    res = app.test_client().get("/staff/furniture/{0}/list.pdf".format(token))
    assert res.status_code == 200 and res.data[:4] == b"%PDF"
    assert res.headers["Content-Type"] == "application/pdf"
    assert res.headers["Content-Disposition"].startswith("inline;") and row["public_id"] in res.headers["Content-Disposition"]
    return True


def test_read_only_and_bad_tokens():
    crew_id, crew = _crew()
    b = _booking(crew)
    _submit(b, _content())
    token = furniture_lists.staff_link_token(b)
    client = app.test_client()
    panel = client.get("/staff/furniture/{0}/panel".format(token)).get_data(as_text=True)
    assert "<form" not in panel and "<input" not in panel and "<button" not in panel
    assert client.post("/staff/furniture/{0}/panel".format(token)).status_code == 405
    assert client.get("/staff/furniture/{0}x/panel".format(token)).status_code == 404
    _c2, crew2 = _crew()
    no_list = _booking(crew2)
    assert client.get("/staff/furniture/{0}/panel".format(furniture_lists.staff_link_token(no_list))).status_code == 404
    # Future per-staff logins: the hook limits a list to that person's jobs.
    booking = db.get_booking(b)
    assert furniture_lists.staff_can_view(booking) and furniture_lists.staff_can_view(booking, crew)
    assert not furniture_lists.staff_can_view(booking, crew2)
    return True


def test_one_query_for_all_cards():
    crew_id, crew = _crew()
    ids = []
    for i in range(6):
        b = _booking(crew, move_date=(perth_today() + timedelta(days=i % 3)).isoformat())
        if i % 2 == 0:
            _submit(b, _content())
        ids.append(b)
    jobs = [{"id": b} for b in ids]
    calls = {"n": 0}
    real = db.get_connection

    def counting():
        calls["n"] += 1
        return real()

    furniture_lists.db.get_connection = counting
    try:
        furniture_lists.attach_staff_links(jobs)
    finally:
        furniture_lists.db.get_connection = real
    assert calls["n"] == 1, calls
    assert [bool(j["furniture"]) for j in jobs] == [True, False, True, False, True, False]
    assert all("rooms" not in (j["furniture"] or {}) for j in jobs), "cards carry counts only"
    return True


def main():
    tests = [
        test_case_a_summary_and_detail,
        test_case_b_not_submitted,
        test_case_c_fifty_plus_items,
        test_case_d_updated_list_shows_latest_and_yellow,
        test_case_e_individual_staff_views,
        test_case_f_all_staff_views,
        test_case_g_mobile_markup,
        test_case_h_pdf_opens,
        test_read_only_and_bad_tokens,
        test_one_query_for_all_cards,
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
