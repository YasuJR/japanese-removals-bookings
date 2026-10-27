#!/usr/bin/env python3
"""E2E tests — customer furniture & items lists (customer link, office, staff, website)."""

import io
import json
import os
import re
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
import furniture_enquiry
import furniture_lists
import furniture_volume_refs as refs
import furniture_volumes
from app import app
from staff_portal import RANGE_TODAY, build_staff_portal

_counter = 0


def _tag():
    global _counter
    _counter += 1
    return "{0}-{1}".format(os.getpid(), _counter)


def _office_client(admin=False):
    db.init_db()
    label = "fl-test-{0}".format(_tag())
    uid = db.create_staff_user(label, auth.hash_password("test"), "Furniture Test", is_admin=1 if admin else 0)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _public_client(ip="10.9.0.1"):
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = ip
    return client


def _booking(name, move_date=None, crew="", notes="furniture test"):
    db.init_db()
    return db.create_booking(
        name, "0400111222", "fl-test@example.com", "1 Test St, Perth", "2 Test Ave, Fremantle",
        move_date or (date.today() + timedelta(days=5)).isoformat(), 2, notes, crew=crew,
        status="Confirmed",
    )


def _token_from_url(url):
    return url.rstrip("/").split("/furniture/")[-1]


def _png_bytes(size=(1200, 800), color=(200, 30, 30)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_with_exif():
    from PIL import Image

    image = Image.new("RGB", (640, 480), (20, 120, 60))
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"  # Make
    exif[0x0112] = 6  # Orientation: rotate 90
    buf = io.BytesIO()
    image.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def _sample_content():
    return {
        "rooms": [
            {"id": "r1", "type": "bedroom", "label": "Bedroom 1", "items": {"bed_queen": 1, "bedside_table": 2}},
            {"id": "r2", "type": "living", "label": "Living room", "items": {"sofa_3": 1, "tv_small": 1, "bogus_item": 4}},
            {"id": "r3", "type": "nope", "label": "Hacked", "items": {"sofa_3": 9}},
        ],
        "custom_items": [
            {"name": "Fish tank", "qty": 1, "length_cm": 120, "width_cm": 50, "height_cm": 60},
            {"name": "Antique clock", "qty": 2},
            {"name": "", "qty": 3},
        ],
        "notes": "2nd floor, no lift. <script>alert(1)</script>",
    }


def test_catalog_math_and_no_double_count():
    content = catalog.sanitize_content(_sample_content())
    assert [r["type"] for r in content["rooms"]] == ["bedroom", "living"]
    assert "bogus_item" not in content["rooms"][1]["items"]
    assert len(content["custom_items"]) == 2
    s = catalog.summarize(content)
    expected = 1.5 + 2 * 0.175 + 2.0 + 0.35 + (1.2 * 0.5 * 0.6)
    assert abs(s["estimated_m3"] - round(expected, 1)) < 1e-9, s
    assert s["item_count"] == 1 + 2 + 1 + 1 + 1 + 2
    assert s["unsized_custom_count"] == 2
    # Bed with mattress, frame only and mattress only are separate, mutually exclusive choices.
    assert catalog.ITEMS["bed_queen"][1] == 1.5
    assert abs(catalog.ITEMS["bed_frame_queen"][1] + catalog.ITEMS["mattress_queen"][1] - 1.5) < 1e-9
    assert "mattress" in catalog.ITEMS["bed_queen"][0].lower()
    # Quantities are clamped.
    clamped = catalog.sanitize_content({"rooms": [{"id": "x", "type": "living", "items": {"sofa_3": 99999}}]})
    assert clamped["rooms"][0]["items"]["sofa_3"] == catalog.MAX_QTY
    return True


def test_booking_link_save_reedit_submit_and_office_view():
    tag = _tag()
    name = "Furniture Customer {0}".format(tag)
    other = "Other Customer {0}".format(tag)
    booking_id = _booking(name)
    _booking(other)
    office = _office_client()

    page = office.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    assert "Furniture list" in page and "Copy input link" in page and "Not started" in page

    res = office.post("/bookings/{0}/furniture/link".format(booking_id)).get_json()
    assert res["ok"] and res["public_id"].startswith("FL-")
    again = office.post("/bookings/{0}/furniture/link".format(booking_id)).get_json()
    assert again["url"] == res["url"], "link must be stable per booking"
    token = _token_from_url(res["url"])
    assert len(token) >= 40

    customer = _public_client()
    html = customer.get("/furniture/{0}".format(token))
    assert html.status_code == 200
    body = html.get_data(as_text=True)
    assert res["public_id"] in body and "Hi Furniture" in body
    assert other not in body and "0400111222" not in body and "1 Test St" not in body
    assert "no-store" in html.headers["Cache-Control"] and "noindex" in html.headers["X-Robots-Tag"]

    save = customer.post("/furniture/{0}/save".format(token), json={"content": _sample_content()}).get_json()
    assert save["ok"] and save["status"] == "in_progress", save
    assert save["estimated_m3"] == 4.6 and save["item_count"] == 8, save
    assert save["units"]["r1"] == {"bed_queen": 1.5, "bedside_table": 0.175}, save["units"]

    # Re-open: saved state comes back.
    body = customer.get("/furniture/{0}".format(token)).get_data(as_text=True)
    data = json.loads(re.search(r'<script type="application/json" id="fl-data">(.*?)</script>', body, re.S).group(1))
    rooms = {r["label"]: r for r in data["state"]["content"]["rooms"]}
    assert rooms["Bedroom 1"]["items"] == {"bed_queen": 1, "bedside_table": 2}
    assert "<script>alert(1)</script>" not in body.split('id="fl-data">')[0]

    # Edit and submit.
    edited = _sample_content()
    edited["rooms"][0]["items"]["bed_queen"] = 2
    sub = customer.post("/furniture/{0}/save".format(token), json={"content": edited, "submit": True}).get_json()
    assert sub["status"] == "submitted" and sub["estimated_m3"] == 6.1, sub
    row = furniture_lists.get_for_booking(booking_id)
    assert row["submitted_at"] and row["last_edited_by"] == "customer"

    # Later customer edits keep Submitted.
    edited["rooms"][0]["items"]["bed_queen"] = 1
    later = customer.post("/furniture/{0}/save".format(token), json={"content": edited}).get_json()
    assert later["status"] == "submitted"

    page = office.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    assert "Submitted" in page and "4.6 m³" in page and res["public_id"] in page
    # Office editor (same list) and office save.
    office_page = office.get("/bookings/{0}/furniture".format(booking_id), follow_redirects=True)
    assert office_page.status_code == 200 and res["public_id"] in office_page.get_data(as_text=True)
    office_save = office.post("/furniture-lists/{0}/save".format(row["id"]), json={"content": {"rooms": [], "custom_items": [], "notes": "office fix"}}).get_json()
    assert office_save["ok"] and office_save["status"] == "submitted" and office_save["estimated_m3"] == 0
    assert furniture_lists.get_by_id(row["id"])["last_edited_by"] == "office"
    listing = office.get("/furniture-lists").get_data(as_text=True)
    assert res["public_id"] in listing
    return True


def test_photos_storage_limits_and_isolation():
    tag = _tag()
    office = _office_client()
    a_id = _booking("Photo A {0}".format(tag))
    b_id = _booking("Photo B {0}".format(tag))
    a_token = _token_from_url(office.post("/bookings/{0}/furniture/link".format(a_id)).get_json()["url"])
    b_token = _token_from_url(office.post("/bookings/{0}/furniture/link".format(b_id)).get_json()["url"])
    client = _public_client()

    up = client.post("/furniture/{0}/photos".format(a_token), data={"photo": (io.BytesIO(_jpeg_with_exif()), "sofa.jpg")}, content_type="multipart/form-data").get_json()
    assert up["ok"], up
    photo_id = up["photo"]["id"]
    # Rotation applied (640x480 with orientation 6 → 480x640) and EXIF stripped.
    assert (up["photo"]["width"], up["photo"]["height"]) == (480, 640), up
    got = client.get("/furniture/{0}/photos/{1}".format(a_token, photo_id))
    assert got.status_code == 200 and got.headers["Content-Type"] == "image/jpeg"
    assert got.headers["X-Content-Type-Options"] == "nosniff"
    assert b"SecretCamera" not in got.data

    big = client.post("/furniture/{0}/photos".format(a_token), data={"photo": (io.BytesIO(_png_bytes((3000, 2400))), "big.png")}, content_type="multipart/form-data").get_json()
    assert big["ok"] and max(big["photo"]["width"], big["photo"]["height"]) == furniture_lists.PHOTO_MAX_EDGE

    bad = client.post("/furniture/{0}/photos".format(a_token), data={"photo": (io.BytesIO(b"%PDF-1.4 not an image"), "doc.pdf")}, content_type="multipart/form-data")
    assert bad.status_code == 400 and not bad.get_json()["ok"]
    svg = client.post("/furniture/{0}/photos".format(a_token), data={"photo": (io.BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"), "x.svg")}, content_type="multipart/form-data")
    assert svg.status_code == 400

    # Other customer's token cannot read or delete list A's photo.
    assert client.get("/furniture/{0}/photos/{1}".format(b_token, photo_id)).status_code == 404
    assert client.post("/furniture/{0}/photos/{1}/delete".format(b_token, photo_id)).get_json()["ok"] is False
    assert client.get("/furniture/{0}/photos/{1}".format("x" * 43, photo_id)).status_code == 404
    # Not logged in → office photo URL redirects to login.
    row_a = furniture_lists.get_for_booking(a_id)
    anon = client.get("/furniture-lists/{0}/photos/{1}".format(row_a["id"], photo_id))
    assert anon.status_code in (302, 401) and b"\xff\xd8" not in anon.data
    # Office can see it.
    assert office.get("/furniture-lists/{0}/photos/{1}".format(row_a["id"], photo_id)).status_code == 200
    assert furniture_lists.get_by_id(row_a["id"])["photo_count"] == 2

    # Photo limit.
    original = furniture_lists.MAX_PHOTOS
    furniture_lists.MAX_PHOTOS = 2
    try:
        over = client.post("/furniture/{0}/photos".format(a_token), data={"photo": (io.BytesIO(_png_bytes()), "c.png")}, content_type="multipart/form-data")
        assert over.status_code == 400 and "up to 2" in over.get_json()["error"]
    finally:
        furniture_lists.MAX_PHOTOS = original

    assert client.post("/furniture/{0}/photos/{1}/delete".format(a_token, photo_id)).get_json()["ok"] is True
    assert client.get("/furniture/{0}/photos/{1}".format(a_token, photo_id)).status_code == 404
    assert furniture_lists.get_by_id(row_a["id"])["photo_count"] == 1
    return True


def test_access_control_for_public_and_office_routes():
    tag = _tag()
    office = _office_client()
    booking_id = _booking("Access {0}".format(tag))
    res = office.post("/bookings/{0}/furniture/link".format(booking_id)).get_json()
    anon = _public_client()
    # Public id alone gives no access.
    assert anon.get("/furniture/{0}".format(res["public_id"])).status_code == 404
    assert anon.get("/furniture/" + "A" * 43).status_code == 404
    assert anon.post("/furniture/{0}/save".format("B" * 43), json={}).status_code == 404
    # Office routes need login.
    for path in ("/furniture-lists", "/bookings/{0}/furniture".format(booking_id)):
        assert anon.get(path).status_code == 302, path
    assert anon.post("/bookings/{0}/furniture/link".format(booking_id)).status_code == 302
    row = furniture_lists.get_for_booking(booking_id)
    assert anon.post("/furniture-lists/{0}/save".format(row["id"]), json={}).status_code == 302
    return True


def test_website_list_return_link_contact_and_linking():
    tag = _tag()
    client = _public_client("10.9.1.{0}".format(_counter % 250))
    start = client.get("/furniture?return=https://japaneseremovals.com.au/contact-us/%3Fa%3D1")
    assert start.status_code == 200 and "Start a new list" in start.get_data(as_text=True)
    created = client.post("/furniture/start", data={"return_url": "https://japaneseremovals.com.au/contact-us/?a=1"})
    assert created.status_code == 302 and "/furniture/" in created.headers["Location"]
    token = _token_from_url(created.headers["Location"])
    row = furniture_lists.get_by_token(token)
    assert row["source"] == "website" and row["booking_id"] is None
    link = furniture_lists.return_link(row)
    assert link.startswith("https://japaneseremovals.com.au/contact-us/?a=1&furniture_list=FL-"), link
    page = client.get("/furniture/{0}".format(token)).get_data(as_text=True)
    assert "Save &amp; Return to Quote" in page and "Your details" in page

    saved = client.post("/furniture/{0}/save".format(token), json={
        "content": {"rooms": [{"id": "r1", "type": "living", "label": "Living", "items": {"sofa_2": 1}}]},
        "contact": {"name": "Web Person {0}".format(tag), "phone": "0400999888", "email": "web@example.com", "move_date": "2026-12-01"},
        "submit": True,
    }).get_json()
    assert saved["status"] == "in_progress" and saved["return_link"] == link  # draft until the enquiry
    row = furniture_lists.get_by_token(token)
    assert row["contact_name"] == "Web Person {0}".format(tag)

    # Open redirects are refused.
    for evil in ("https://evil.example.com/", "http://japaneseremovals.com.au/", "javascript:alert(1)", "https://japaneseremovals.com.au.evil.com/"):
        assert furniture_lists.safe_return_url(evil) == "", evil

    # Booking created later with the reference in its notes links automatically.
    office = _office_client()
    booking_id = _booking("Website Booking {0}".format(tag), notes="Quote request.\n\nFurniture list: {0}".format(row["public_id"].lower()))
    page = office.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    assert row["public_id"] in page and "In progress" in page
    assert furniture_lists.get_for_booking(booking_id)["id"] == row["id"]

    # Manual link by code, and the one-list-per-booking rule.
    second = furniture_lists.create_list(furniture_lists.SOURCE_WEBSITE)
    other_booking = _booking("Manual Link {0}".format(tag))
    office.post("/bookings/{0}/furniture/attach".format(other_booking), data={"code": second["public_id"]})
    assert furniture_lists.get_for_booking(other_booking)["id"] == second["id"]
    third = furniture_lists.create_list(furniture_lists.SOURCE_WEBSITE)
    try:
        furniture_lists.link_to_booking(third["id"], other_booking)
        raise AssertionError("second list linked to the same booking")
    except furniture_lists.FurnitureError:
        pass
    return True


def test_staff_portal_link_only_for_assigned_jobs():
    tag = _tag()
    crew_name = "Yasu"
    today = date.today()
    with_list = _booking("Staff Furniture {0}".format(tag), move_date=today.isoformat(), crew=crew_name)
    without_list = _booking("Staff NoList {0}".format(tag), move_date=today.isoformat(), crew=crew_name)
    row = furniture_lists.ensure_for_booking(with_list)
    furniture_lists.save_content(row, {"content": _sample_content()}, editor="customer")
    photo = furniture_lists.add_photo(furniture_lists.get_by_id(row["id"]), _png_bytes(), uploaded_by="customer")

    portal = build_staff_portal(staff_name=crew_name, range_key=RANGE_TODAY, today=today)
    jobs = {job["id"]: job for job in portal["jobs"]}
    assert jobs[with_list].get("furniture_token"), "assigned job with a list gets a staff link"
    assert not jobs[without_list].get("furniture_token")

    token = jobs[with_list]["furniture_token"]
    assert furniture_lists.booking_id_from_staff_token(token) == with_list
    client = _public_client()
    page = client.get("/staff/furniture/{0}".format(token))
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Queen bed + mattress" in text and "Fish tank" in text and row["public_id"] in text
    assert "&lt;script&gt;" in text and "<script>alert(1)</script>" not in text
    assert client.get("/staff/furniture/{0}/photos/{1}".format(token, photo["id"])).status_code == 200
    assert client.get("/staff/furniture/{0}x".format(token)).status_code == 404
    assert client.get("/staff/furniture/{0}".format(furniture_lists.staff_link_token(without_list))).status_code == 404

    html = client.get("/staff?staff_id=&range=today").get_data(as_text=True)
    assert html is not None
    return True


def test_website_list_rate_limit():
    ip = "10.77.{0}.{1}".format(os.getpid() % 250, _counter % 250)
    client = _public_client(ip)
    for _ in range(furniture_lists.WEBSITE_LISTS_PER_IP_PER_HOUR):
        assert client.post("/furniture/start").status_code == 302
    blocked = client.post("/furniture/start")
    assert blocked.status_code == 302 and "error=limit" in blocked.headers["Location"]
    return True


def test_booking_view_without_list_and_invoice_page_still_work():
    office = _office_client()
    booking_id = _booking("Plain Booking {0}".format(_tag()))
    assert office.get("/bookings/{0}".format(booking_id)).status_code == 200
    assert office.get("/bookings/{0}/edit".format(booking_id)).status_code == 200
    assert office.get("/bookings/{0}/invoice/preview".format(booking_id)).status_code == 200
    assert furniture_lists.get_for_booking(booking_id) is None, "viewing must not create a list"
    return True


def test_volume_references_are_consistent():
    assert refs.CHECKED_ON == "2026-10-27"
    assert set(refs.REFS) == set(catalog.ITEMS), "every item needs a reference entry"
    for key, ref in refs.REFS.items():
        basis = ref["basis"]
        used = [src for src, _label, _m3 in ref["refs"]]
        assert len(used) == len(set(used)), (key, "one value per source")
        for src in used:
            assert refs.SOURCES[src][2] == refs.REMOVALIST, (key, src, "load volumes only from removalists")
        for src, _label, _value, why in ref["excluded"]:
            assert src in refs.SOURCES and why, key
        for src, _label, dims, outer in ref["outer"]:
            assert src in refs.SOURCES and dims and outer > 0, key
        assert ref["reason"], key
        if basis == refs.MEDIAN:
            assert len(used) >= 2, key
        elif basis == refs.SINGLE:
            assert len(used) == 1, key
        elif basis == refs.DERIVED:
            assert not used and ref["derived_from"], key
        else:
            assert basis == refs.PROVISIONAL and not used and ref["value"], key
        assert 0 < catalog.ITEMS[key][1] <= catalog.MAX_UNIT_M3, key
    for name, url, kind, note in refs.SOURCES.values():
        assert url.startswith("https://") and name and note and kind in (refs.REMOVALIST, refs.PRODUCT)
    # Frame only + mattress only == bed with mattress, for every size (no double count, no gap).
    for size in ("single", "king_single", "double", "queen", "king"):
        whole = catalog.ITEMS["bed_" + size][1]
        parts = catalog.ITEMS["bed_frame_" + size][1] + catalog.ITEMS["mattress_" + size][1]
        assert abs(whole - parts) < 1e-9, size
    expected = {
        "sofa_3": 2.0, "bed_queen": 1.5, "mattress_queen": 0.8, "bed_frame_queen": 0.7,
        "fridge_double": 1.15, "fridge_large": 1.6, "washing_machine": 0.6,
        "box_small": 0.05, "box_medium": 0.08, "box_large": 0.15, "box_wardrobe": 0.35,
    }
    for key, value in expected.items():
        assert catalog.ITEMS[key][1] == value, (key, catalog.ITEMS[key][1])
    # Old values are frozen for lists saved before the review.
    assert catalog.LEGACY_M3["bed_queen"] == 1.8 and catalog.LEGACY_M3["sofa_3"] == 1.7
    return True


def _set_legacy_content(list_id, content, m3):
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE furniture_lists SET content_json = ?, estimated_m3 = ?, item_count = ?, status = ? WHERE id = ?",
            (json.dumps(content), m3, 2, "submitted", list_id),
        )
        conn.commit()


def test_existing_lists_keep_numbers_and_admin_change_applies_to_new_items():
    office = _office_client()
    booking_id = _booking("Volume Snapshot {0}".format(_tag()))
    res = office.post("/bookings/{0}/furniture/link".format(booking_id)).get_json()
    token = _token_from_url(res["url"])
    row = furniture_lists.get_for_booking(booking_id)
    # A list saved before the review: no per-item m³ stored, old totals.
    legacy = {"rooms": [{"id": "r1", "type": "bedroom", "label": "Bedroom 1", "items": {"bed_queen": 1}},
                        {"id": "r2", "type": "living", "label": "Living", "items": {"sofa_3": 1}}],
              "custom_items": [], "notes": ""}
    _set_legacy_content(row["id"], legacy, 3.5)
    loaded = furniture_lists.get_by_id(row["id"])
    assert loaded["content"]["rooms"][0]["unit_m3"] == {"bed_queen": 1.8}
    assert loaded["estimated_m3"] == 3.5
    assert catalog.summarize(loaded["content"])["estimated_m3"] == 3.5

    customer = _public_client("10.9.7.1")
    body = customer.get("/furniture/{0}".format(token)).get_data(as_text=True)
    data = json.loads(re.search(r'id="fl-data">(.*?)</script>', body, re.S).group(1))
    assert data["state"]["content"]["rooms"][0]["unit_m3"] == {"bed_queen": 1.8}
    assert data["catalog"]["items"]["bed_queen"]["m3"] == 1.5

    # Re-saving unchanged keeps 3.5, even if the browser tries to send its own volumes.
    tampered = json.loads(json.dumps(legacy))
    tampered["rooms"][0]["unit_m3"] = {"bed_queen": 0.01}
    same = customer.post("/furniture/{0}/save".format(token), json={"content": tampered}).get_json()
    assert same["estimated_m3"] == 3.5 and same["units"]["r1"] == {"bed_queen": 1.8}, same
    assert same["status"] == "submitted"

    admin = _office_client(admin=True)
    try:
        r = admin.post("/furniture-lists/volumes", data={"item_key": "bed_queen", "m3": "1.65", "note": "test"})
        assert r.status_code == 302
        assert furniture_volumes.effective_volumes()["bed_queen"] == 1.65
        # Existing item keeps 1.8; more of the same item in the same room keeps 1.8 too.
        grown = json.loads(json.dumps(legacy))
        grown["rooms"][0]["items"]["bed_queen"] = 2
        # A new item and the same item in a new room use today's standard.
        grown["rooms"][1]["items"]["washing_machine"] = 1
        grown["rooms"].append({"id": "r9", "type": "bedroom", "label": "Bedroom 2", "items": {"bed_queen": 1}})
        out = customer.post("/furniture/{0}/save".format(token), json={"content": grown}).get_json()
        assert out["units"]["r1"] == {"bed_queen": 1.8}, out["units"]
        assert out["units"]["r2"] == {"sofa_3": 1.7, "washing_machine": 0.6}, out["units"]
        assert out["units"]["r9"] == {"bed_queen": 1.65}, out["units"]
        assert out["estimated_m3"] == round(1.8 * 2 + 1.7 + 0.6 + 1.65, 1), out

        # Removing an item and adding it back later uses the standard of that time.
        removed = json.loads(json.dumps(grown))
        del removed["rooms"][1]["items"]["sofa_3"]
        customer.post("/furniture/{0}/save".format(token), json={"content": removed})
        back = customer.post("/furniture/{0}/save".format(token), json={"content": grown}).get_json()
        assert back["units"]["r2"]["sofa_3"] == 2.0, back["units"]

        # Another, untouched list is never rewritten by the admin change.
        other_booking = _booking("Volume Untouched {0}".format(_tag()))
        office.post("/bookings/{0}/furniture/link".format(other_booking))
        other = furniture_lists.get_for_booking(other_booking)
        _set_legacy_content(other["id"], legacy, 3.5)
        assert furniture_lists.get_by_id(other["id"])["estimated_m3"] == 3.5

        # Office view and staff summary use the stored per-item m³.
        page = office.get("/furniture-lists/{0}".format(row["id"])).get_data(as_text=True)
        assert row["public_id"] in page
    finally:
        admin.post("/furniture-lists/volumes", data={"item_key": "bed_queen", "action": "reset"})
    assert furniture_volumes.effective_volumes()["bed_queen"] == 1.5
    return True


def test_standard_volume_admin_page_permissions_and_validation():
    anon = app.test_client()
    r = anon.get("/furniture-lists/volumes")
    assert r.status_code == 302 and "/login" in r.headers["Location"]

    staff = _office_client()
    page = staff.get("/furniture-lists/volumes")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "View only" in html
    assert "https://yourmovingsolution.com.au/furniture-volume-calculator/" in html
    assert "Provisional (no source)" in html and "Product size (outer volume" in html
    assert 'name="m3"' not in html
    assert staff.post("/furniture-lists/volumes", data={"item_key": "sofa_3", "m3": "9"}).status_code == 403
    assert "sofa_3" not in furniture_volumes.overrides()

    admin = _office_client(admin=True)
    html = admin.get("/furniture-lists/volumes").get_data(as_text=True)
    assert 'name="m3"' in html and "View only" not in html
    try:
        for bad in ("abc", "0", "-1", "25", ""):
            r = admin.post("/furniture-lists/volumes", data={"item_key": "sofa_3", "m3": bad}, follow_redirects=True)
            assert "must be between" in r.get_data(as_text=True) or "Enter a number" in r.get_data(as_text=True), bad
        assert "sofa_3" not in furniture_volumes.overrides()
        assert admin.post("/furniture-lists/volumes", data={"item_key": "nope", "m3": "1"}, follow_redirects=True).status_code == 200
        r = admin.post("/furniture-lists/volumes", data={"item_key": "sofa_3", "m3": "2.25", "note": "Measured on our jobs"}, follow_redirects=True)
        html = r.get_data(as_text=True)
        assert "standard volume is now 2.25" in html and "Changed by admin" in html and "Measured on our jobs" in html
        assert furniture_volumes.overrides()["sofa_3"]["m3"] == 2.25
        row = [i for g in furniture_volumes.admin_rows() for i in g["items"] if i["key"] == "sofa_3"][0]
        assert re.match(r"^[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2} \d{4}, ", row["updated_display"]), row["updated_display"]
        changes = furniture_volumes.recent_changes(5)
        assert changes[0]["item_key"] == "sofa_3" and changes[0]["old_m3"] == 2.0 and changes[0]["new_m3"] == 2.25
        # New lists pick it up in the editor catalogue.
        customer = _public_client("10.9.8.1")
        start = customer.post("/furniture/start")
        body = customer.get(start.headers["Location"]).get_data(as_text=True)
        data = json.loads(re.search(r'id="fl-data">(.*?)</script>', body, re.S).group(1))
        assert data["catalog"]["items"]["sofa_3"]["m3"] == 2.25
        # Setting the researched value again clears the override.
        admin.post("/furniture-lists/volumes", data={"item_key": "sofa_3", "m3": "2.0"})
        assert "sofa_3" not in furniture_volumes.overrides()
        assert furniture_volumes.recent_changes(1)[0]["action"] == "reset"
    finally:
        admin.post("/furniture-lists/volumes", data={"item_key": "sofa_3", "action": "reset"})
    assert furniture_volumes.effective_volumes()["sofa_3"] == 2.0
    return True


def _pdf_text(pdf_bytes):
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def _website_list(client, content=None):
    start = client.post("/furniture/start")
    assert start.status_code == 302
    token = start.headers["Location"].rstrip("/").split("/furniture/")[-1]
    row = furniture_lists.get_by_token(token)
    if content is not None:
        client.post("/furniture/{0}/save".format(token), json={"content": content})
    return token, furniture_lists.get_by_token(token)


def test_quote_flow_list_is_a_draft_with_save_and_return():
    customer = _public_client("10.9.20.1")
    token, row = _website_list(customer)
    html = customer.get("/furniture/{0}".format(token)).get_data(as_text=True)
    assert "Save &amp; Return to Quote" in html and 'id="fl-submit"' not in html and "Send list" not in html
    assert "Draft — sent with your quote request" in html
    # No return address given → back to the website quote form, carrying the FL reference.
    assert "https://japaneseremovals.com.au/contact-us/?furniture_list={0}#quote".format(row["public_id"]) in html
    # Saving — even with submit=True from the browser — never submits a website list.
    res = customer.post("/furniture/{0}/save".format(token), json={"content": _sample_content(), "submit": True}).get_json()
    assert res["ok"] and res["status"] == "in_progress", res
    assert res["status_label"] == "Draft — sent with your quote request"
    row = furniture_lists.get_by_token(token)
    assert row["status"] == "in_progress" and not row["submitted_at"] and not row["enquiry_received_at"]
    # The flow-A code paths have no email/SMS sending at all.
    for module in ("furniture_lists.py", "furniture_enquiry.py", "furniture_pdf.py"):
        source = (ROOT / module).read_text()
        assert not re.search(r"(?i)\b(sms|smtp|send_email|twilio|invoice_send|gmail)\b", source), module
    # Flow B (existing booking link) is unchanged: Send list submits.
    office = _office_client()
    booking_id = _booking("Flow B {0}".format(_tag()))
    link = office.post("/bookings/{0}/furniture/link".format(booking_id)).get_json()
    btoken = _token_from_url(link["url"])
    bhtml = customer.get("/furniture/{0}".format(btoken)).get_data(as_text=True)
    assert "Send list" in bhtml and "Save &amp; Return to Quote" not in bhtml
    sub = customer.post("/furniture/{0}/save".format(btoken), json={"content": _sample_content(), "submit": True}).get_json()
    assert sub["status"] == "submitted"
    return True


def test_enquiry_pdf_api_security_receipt_and_dedupe():
    key = "test-enquiry-key-{0}".format(_tag())
    old = os.environ.pop("FURNITURE_ENQUIRY_KEY", None)
    api = app.test_client()
    api.environ_base["REMOTE_ADDR"] = "10.9.30.{0}".format(_counter % 200 + 1)
    try:
        customer = _public_client("10.9.21.1")
        token, row = _website_list(customer, _sample_content())
        pid = row["public_id"]
        url = "/api/enquiry-list/{0}.pdf".format(pid)
        assert api.get(url, headers={"X-JR-Enquiry-Key": key}).status_code == 503, "disabled without a server key"
        os.environ["FURNITURE_ENQUIRY_KEY"] = key
        assert api.get(url).status_code == 401
        assert api.get(url, headers={"X-JR-Enquiry-Key": "wrong"}).status_code == 401
        assert api.get("/api/enquiry-list/FL-ZZZZZZ.pdf", headers={"X-JR-Enquiry-Key": key}).status_code == 404
        assert api.get("/api/enquiry-list/nonsense.pdf", headers={"X-JR-Enquiry-Key": key}).status_code == 404
        # A list made for an existing booking (flow B) is never served, even with the key.
        office = _office_client()
        booking_id = _booking("Enquiry Other {0}".format(_tag()))
        other = office.post("/bookings/{0}/furniture/link".format(booking_id)).get_json()["public_id"]
        assert api.get("/api/enquiry-list/{0}.pdf".format(other), headers={"X-JR-Enquiry-Key": key}).status_code == 404
        assert furniture_lists.get_by_token(token)["status"] == "in_progress", "nothing received before the enquiry"

        customer.post("/furniture/{0}/photos".format(token), data={"photo": (io.BytesIO(_png_bytes()), "sofa.png")}, content_type="multipart/form-data")
        res = api.get(url.lower(), headers={"X-JR-Enquiry-Key": key})
        assert res.status_code == 200 and res.headers["Content-Type"] == "application/pdf"
        assert res.data[:4] == b"%PDF" and "no-store" in res.headers["Cache-Control"]
        text = _pdf_text(res.data)
        for needle in (pid, "Bedroom 1", "Queen bed + mattress", "3-seater sofa", "Fish tank", "has photos (1)",
                       "写真あり", "推定値", "2nd floor, no lift", "approx. 4.6 m³"):
            assert needle in text, (needle, text[:800])
        received = furniture_lists.get_by_token(token)
        assert received["status"] == "submitted" and received["enquiry_received_at"] and received["submitted_at"]
        assert received["last_edited_by"] == "website enquiry" and received["enquiry_fetch_count"] == 1
        # Customer view now says it was received; office view shows the enquiry.
        assert "Received with your quote request" in customer.get("/furniture/{0}".format(token)).get_data(as_text=True)
        assert "Received " in office.get("/furniture-lists/{0}".format(received["id"])).get_data(as_text=True)
        # Retry / double send of the same enquiry: same PDF, received only once.
        again = api.get(url, headers={"X-JR-Enquiry-Key": key})
        assert again.status_code == 200 and again.data[:4] == b"%PDF"
        twice = furniture_lists.get_by_token(token)
        assert twice["enquiry_fetch_count"] == 2 and twice["enquiry_received_at"] == received["enquiry_received_at"]
        # The same reference can't ride along with a later, different enquiry.
        with db.get_connection() as conn:
            conn.execute("UPDATE furniture_lists SET enquiry_received_at = ? WHERE id = ?", ("2026-01-01T00:00:00Z", received["id"]))
            conn.commit()
        assert api.get(url, headers={"X-JR-Enquiry-Key": key}).status_code == 409
        # Every request is logged.
        with db.get_connection() as conn:
            rows = conn.execute("SELECT status_code FROM furniture_api_log WHERE public_id = ?", (pid,)).fetchall()
        codes = sorted(int(dict(r)["status_code"]) for r in rows)
        assert codes.count(200) == 2 and 401 in codes and 409 in codes and 503 in codes, codes
        # Repeated bad keys from one IP lock that IP out, even with the right key afterwards.
        brute = app.test_client()
        brute.environ_base["REMOTE_ADDR"] = "10.9.31.{0}".format(_counter % 200 + 1)
        for _ in range(furniture_enquiry.BAD_KEYS_PER_IP_PER_HOUR):
            assert brute.get(url, headers={"X-JR-Enquiry-Key": "guess"}).status_code == 401
        assert brute.get(url, headers={"X-JR-Enquiry-Key": key}).status_code == 429
    finally:
        if old is None:
            os.environ.pop("FURNITURE_ENQUIRY_KEY", None)
        else:
            os.environ["FURNITURE_ENQUIRY_KEY"] = old
    return True


def test_empty_website_list_pdf_still_valid():
    key = "test-enquiry-key-empty"
    os.environ["FURNITURE_ENQUIRY_KEY"] = key
    try:
        customer = _public_client("10.9.22.1")
        _token, row = _website_list(customer)
        api = app.test_client()
        api.environ_base["REMOTE_ADDR"] = "10.9.32.1"
        res = api.get("/api/enquiry-list/{0}.pdf".format(row["public_id"]), headers={"X-JR-Enquiry-Key": key})
        assert res.status_code == 200 and res.data[:4] == b"%PDF"
        text = _pdf_text(res.data)
        assert "No items entered" in text and "写真なし" in text and row["public_id"] in text
    finally:
        os.environ.pop("FURNITURE_ENQUIRY_KEY", None)
    return True


def main():
    tests = [
        test_quote_flow_list_is_a_draft_with_save_and_return,
        test_enquiry_pdf_api_security_receipt_and_dedupe,
        test_empty_website_list_pdf_still_valid,
        test_catalog_math_and_no_double_count,
        test_volume_references_are_consistent,
        test_existing_lists_keep_numbers_and_admin_change_applies_to_new_items,
        test_standard_volume_admin_page_permissions_and_validation,
        test_booking_link_save_reedit_submit_and_office_view,
        test_photos_storage_limits_and_isolation,
        test_access_control_for_public_and_office_routes,
        test_website_list_return_link_contact_and_linking,
        test_staff_portal_link_only_for_assigned_jobs,
        test_website_list_rate_limit,
        test_booking_view_without_list_and_invoice_page_still_work,
    ]
    passed = 0
    for test in tests:
        try:
            if test():
                print("PASS:", test.__name__)
                passed += 1
            else:
                print("FAIL:", test.__name__)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            print("FAIL:", test.__name__, "—", repr(exc))
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
