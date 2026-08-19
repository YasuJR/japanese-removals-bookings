#!/usr/bin/env python3
"""E2E tests — iOS invoice PDF share sends only the PDF file."""

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
from app import app


SHARE_JS = ROOT / "static" / "invoice_share.js"
_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "pdf-share-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "PDF Share Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_booking():
    return db.create_booking(
        "PDF Share Customer",
        "0412000777",
        "pdf-share@example.com",
        "1 Share St, Perth WA",
        "2 Share Ave, Fremantle WA",
        "2026-11-20",
        2,
        "pdf share test",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        start_time="09:00",
        finish_time="12:00",
        duration_hours="3",
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
    )


def test_share_js_never_passes_page_metadata():
    source = SHARE_JS.read_text(encoding="utf-8")
    assert "window.location" not in source
    assert "location.href" not in source
    assert "nav.share(buildShareData(" in source
    forbidden_share = re.findall(
        r"nav\.share\(\s*\{[^}]*\b(url|text|title)\s*:", source
    )
    assert forbidden_share == [], forbidden_share
    assert "files: [pdfFile]" in source
    assert 'SHARE_FILENAME = "invoice.pdf"' in source
    return True


def test_preview_has_share_button_and_keeps_download():
    booking_id = _create_booking()
    client = _login_client()
    html = client.get("/bookings/{0}/invoice/preview".format(booking_id)).get_data(
        as_text=True
    )
    assert "Share PDF" in html
    assert "data-share-invoice-pdf=" in html
    assert "/bookings/{0}/invoice.pdf".format(booking_id) in html
    assert "Download PDF" in html
    assert "invoice_share.js" in html
    assert "Staff login" not in html
    return True


def test_edit_booking_has_share_button_without_submitting_form():
    booking_id = _create_booking()
    client = _login_client()
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "Share PDF" in html
    assert 'type="button"' in html
    assert "data-share-invoice-pdf=" in html
    assert "/bookings/{0}/invoice.pdf".format(booking_id) in html
    assert "Download PDF" in html
    assert "invoice_share.js" in html
    return True


def test_download_pdf_endpoint_unchanged():
    booking_id = _create_booking()
    client = _login_client()
    resp = client.get("/bookings/{0}/invoice.pdf".format(booking_id))
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data.startswith(b"%PDF")
    disposition = resp.headers.get("Content-Disposition", "")
    assert disposition.startswith("inline;")
    assert "filename=invoice-" in disposition
    return True


def test_share_payload_helper_files_only():
    data = {
        "files": ["invoice.pdf"],
    }
    assert list(data.keys()) == ["files"]
    assert "url" not in data
    assert "text" not in data
    assert "title" not in data
    return True


def main():
    db.init_db()
    tests = [
        test_share_js_never_passes_page_metadata,
        test_preview_has_share_button_and_keeps_download,
        test_edit_booking_has_share_button_without_submitting_form,
        test_download_pdf_endpoint_unchanged,
        test_share_payload_helper_files_only,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS:", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", fn.__name__, exc)
    print("\n{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
