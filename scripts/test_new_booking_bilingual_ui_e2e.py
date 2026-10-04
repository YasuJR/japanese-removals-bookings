#!/usr/bin/env python3
"""New Booking bilingual UI: English labels + Japanese sublabels (display only)."""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as app_module
import auth
import database as db

app = app_module.app


def _login(client, tag=""):
    db.init_db()
    uid = db.create_staff_user(
        "bilingual-ui-{0}-{1}".format(os.getpid(), tag or os.urandom(4).hex()),
        auth.hash_password("test"),
        "Bilingual UI",
    )
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "bilingual-ui"
    return uid


def test_new_booking_bilingual_sublabels():
    client = app.test_client()
    _login(client, "new")
    html = client.get("/bookings/new").get_data(as_text=True)

    assert 'class="field-label-ja"' in html
    assert 'lang="ja"' in html
    assert "新規予約" in html
    assert "顧客情報を貼り付け" in html
    assert "内容を解析" in html
    assert "引越し元住所" in html
    assert "引越し先住所" in html
    assert "料金設定" in html
    assert "作業コスト" in html
    assert "Pickup address" in html or "Pickup address *" in html
    assert 'name="customer_name"' in html
    assert 'name="pickup_address"' in html
    assert "例：2/155 Stock Rd" in html
    return True


def test_analyse_paste_shows_bilingual_extracted_labels():
    client = app.test_client()
    _login(client, "paste")
    paste = """Hi Yasu, this is John Smith
0412 345 678
john@example.com
10 ABC St, Cannington
moving to 25 XYZ Rd, Innaloo"""
    html = client.post(
        "/bookings/new",
        data={"action": "analyse_paste", "paste_text": paste},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Extracted details" in html
    assert "抽出された情報" in html
    assert 'field-label-en">Name' in html
    assert "名前" in html
    assert 'value="John Smith"' in html or "John Smith" in html
    assert "Confirm Booking" in html
    assert "予約を確定" in html
    return True


def test_edit_booking_shared_form_bilingual():
    client = app.test_client()
    _login(client, "edit")
    bid = db.create_booking(
        "Edit Bilingual Test",
        "0400000000",
        "edit-bilingual@example.com",
        "1 Test St, Perth WA",
        "2 Test Rd, Perth WA",
        "2026-12-01",
        2,
        "",
    )
    html = client.get("/bookings/{0}/edit".format(bid)).get_data(as_text=True)
    assert "使用トラック" in html
    assert "Truck assigned" in html
    return True


def main():
    tests = [
        ("new_booking_bilingual_sublabels", test_new_booking_bilingual_sublabels),
        ("analyse_paste_bilingual_extracted", test_analyse_paste_shows_bilingual_extracted_labels),
        ("edit_booking_shared_form_bilingual", test_edit_booking_shared_form_bilingual),
    ]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("OK", name)
        except Exception as exc:
            failed.append((name, exc))
            print("FAIL", name, exc)
    if failed:
        sys.exit(1)
    print("All bilingual UI tests passed.")


if __name__ == "__main__":
    main()
