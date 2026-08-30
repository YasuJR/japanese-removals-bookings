#!/usr/bin/env python3
"""E2E — Save Changes on Edit Booking also refreshes the invoice."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import invoice
import services
from app import app
from integrations import invoice_pdf


_test_user_counter = 0


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    label = "save-unify-{0}-{1}".format(os.getpid(), _test_user_counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "Save Unify Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _create_booking(**kwargs):
    return db.create_booking(
        kwargs.get("customer_name", "Save Unify Customer"),
        kwargs.get("phone", "0412000999"),
        kwargs.get("email", "save-unify@example.com"),
        "1 Unify St, Perth WA",
        "2 Unify Ave, Fremantle WA",
        kwargs.get("move_date", "2026-09-12"),
        2,
        "save unifies invoice",
        hourly_rate=kwargs.get("hourly_rate", 180.0),
        callout_fee=kwargs.get("callout_fee", 90.0),
        gst_enabled=1,
        start_time=kwargs.get("start_time", "08:00"),
        finish_time=kwargs.get("finish_time", "09:00"),
        duration_hours=kwargs.get("duration_hours", "1"),
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
        crew=kwargs.get("crew", "Yasu"),
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
        "start_time": row["start_time"] or "08:00",
        "finish_time": row["finish_time"] or "10:00",
        "duration_hours": row["duration_hours"] or "2",
        "hourly_rate": str(row["hourly_rate"] if row["hourly_rate"] is not None else 180),
        "callout_fee": str(row["callout_fee"] if row["callout_fee"] is not None else 90),
        "gst_enabled": "on",
        "payment_status": row["payment_status"] or "Unpaid",
        "invoice_status": row["invoice_status"] or "",
        "status": "Confirmed",
        "crew": row["crew"] or "Yasu",
        "staff_cost": "72",
        "fuel_cost": "30",
        "action": "save",
        "double_booking_override_confirm": "on",
    }
    base.update(overrides)
    return base


def _control_labels(html):
    import re

    chunks = re.findall(
        r"<(?:a|button)\b[^>]*>(.*?)</(?:a|button)>",
        html,
        flags=re.I | re.S,
    )
    labels = []
    for chunk in chunks:
        text = re.sub(r"<[^>]+>", " ", chunk)
        text = " ".join(text.split())
        if text:
            labels.append(text)
    return labels


def test_edit_page_keeps_three_primary_buttons():
    booking_id = _create_booking()
    client = _login_client()
    html = client.get("/bookings/{0}/edit".format(booking_id)).get_data(as_text=True)
    assert "Download PDF" in html
    assert "Save Changes" in html
    assert "Delete booking" in html
    assert "Invoice overrides" in html
    labels = _control_labels(html)
    for removed in (
        "Invoice Preview",
        "Update Invoice",
        "Send Invoice",
        "Share PDF",
        "Cancel",
    ):
        assert removed not in labels, removed
    assert "invoice-workflow-bar" not in html
    assert "invoice-send-btn" not in html
    assert "invoice-send-blocked" not in html
    assert "invoice-send-status" not in html
    assert 'name="action" value="update_invoice"' not in html
    assert 'name="action" value="send_invoice"' not in html
    assert "invoice_send.js" not in html
    assert "Enter a valid customer email or mobile number" not in html
    assert "Enter a valid customer email, or add a customer mobile number" not in html
    assert "Customer email or phone number required." not in html
    actions = html.split("booking-save-actions", 1)[-1].split("Invoice overrides", 1)[0]
    action_labels = _control_labels(actions)
    assert action_labels == ["Download PDF", "Save Changes", "Delete booking…"]
    return True


def test_one_save_updates_booking_and_invoice():
    booking_id = _create_booking()
    assert not (dict(db.get_booking(booking_id)).get("invoice_number") or "").strip()
    client = _login_client()
    resp = client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(
            booking_id,
            hourly_rate="200",
            callout_fee="80",
            duration_hours="2",
            finish_time="10:00",
            start_time="08:00",
            customer_name="Saved After One Click",
        ),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Changes saved successfully" in body
    assert "Invoice updated" in body
    row = dict(db.get_booking(booking_id))
    assert row["customer_name"] == "Saved After One Click"
    assert float(row["hourly_rate"]) == 200.0
    assert float(row["callout_fee"]) == 80.0
    assert str(row["duration_hours"]) == "2"
    assert (row.get("invoice_number") or "").strip()
    assert row.get("staff_cost") is not None
    assert float(row.get("fuel_cost") or 0) == 30.0
    booking = services.booking_to_dict(db.get_booking(booking_id))
    booking["extra_charges"] = db.list_extra_charges(booking_id)
    doc = invoice_pdf.build_invoice_document(booking)
    assert doc["invoice_number"].startswith("INV")
    assert doc["bank"]["payment_reference"] == doc["invoice_number"]
    pdf_resp = client.get("/bookings/{0}/invoice.pdf".format(booking_id))
    assert pdf_resp.status_code == 200
    assert pdf_resp.data.startswith(b"%PDF")
    assert pdf_resp.headers.get("Content-Disposition", "").startswith("attachment;")
    return True


def test_save_syncs_linked_xero_draft():
    booking_id = _create_booking()
    db.update_booking_integration_fields(
        booking_id, {"xero_invoice_id": "00000000-0000-0000-0000-000000000099"}
    )
    client = _login_client()
    with patch("integrations.xero.is_ready", return_value=True), patch(
        "integrations.xero.is_real_invoice_id", return_value=True
    ), patch("integrations.xero.is_draft_invoice", return_value=True), patch(
        "integrations.xero.sync_invoice_record",
        return_value=(True, "Xero draft updated.", {}),
    ) as sync_mock:
        resp = client.post(
            "/bookings/{0}/edit".format(booking_id),
            data=_form(booking_id, hourly_rate="190"),
            follow_redirects=True,
        )
    assert resp.status_code == 200
    assert sync_mock.called
    assert "Xero draft updated" in resp.get_data(as_text=True)
    assert float(dict(db.get_booking(booking_id))["hourly_rate"]) == 190.0
    return True


def test_save_does_not_rewrite_existing_invoice_number():
    booking_id = _create_booking()
    db.update_booking_invoice_fields(booking_id, {"invoice_number": "45"})
    client = _login_client()
    client.post(
        "/bookings/{0}/edit".format(booking_id),
        data=_form(booking_id, hourly_rate="210"),
        follow_redirects=True,
    )
    row = dict(db.get_booking(booking_id))
    assert row["invoice_number"] == "45"
    assert float(row["hourly_rate"]) == 210.0
    return True


def main():
    db.init_db()
    tests = [
        test_edit_page_keeps_three_primary_buttons,
        test_one_save_updates_booking_and_invoice,
        test_save_syncs_linked_xero_draft,
        test_save_does_not_rewrite_existing_invoice_number,
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
