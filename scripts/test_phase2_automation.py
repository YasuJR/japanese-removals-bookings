#!/usr/bin/env python3
"""Phase 2 post-payment automation — local E2E test with mocked SMS."""

import json
import hmac
import hashlib
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase2"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import config
import database as db
from integrations import review_automation, sms_config, stripe_config
from integrations.stripe import create_checkout_session, handle_webhook_event
import automation


def _mock_sms_send(booking, body, **kwargs):
    return True, "SMS sent (test mock) to {0}".format(booking.get("phone")), "SMtest_mock_sid"


def _signed_webhook(
    session_id: str,
    booking_id: int,
    *,
    card_total: float = 1.02,
    payment_intent: str = "pi_phase2_test_payment",
    event_id: str = "evt_phase2_test",
) -> tuple:
    whsec = stripe_config.get_webhook_secret()
    timestamp = int(time.time())
    base = round(card_total - 0.02, 2)
    event = {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "created": timestamp,
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "amount_total": int(round(card_total * 100)),
                "currency": "aud",
                "metadata": {
                    "booking_id": str(booking_id),
                    "invoice_number": "INV-PHASE2-TEST",
                    "base_total": str(base),
                    "surcharge_amount": "0.02",
                    "card_total": str(card_total),
                },
                "payment_intent": payment_intent,
                "payment_status": "paid",
                "status": "complete",
                "created": timestamp,
            }
        },
    }
    payload = json.dumps(event).encode("utf-8")
    signed = "{0}.{1}".format(timestamp, payload.decode("utf-8")).encode("utf-8")
    sig = hmac.new(whsec.encode(), signed, hashlib.sha256).hexdigest()
    header = "t={0},v1={1}".format(timestamp, sig)
    return payload, header


def _logs_for(booking_id: int, limit: int = 50):
    return [
        e
        for e in db.list_automation_logs(limit=limit)
        if e.get("booking_id") == booking_id
    ]


def _log_summary(entries):
    return [
        {
            "type": e.get("automation_type"),
            "status": e.get("status"),
            "message": e.get("message"),
        }
        for e in entries
    ]


def main() -> int:
    db.init_db()
    results = {}

    bid = db.create_booking(
        "Phase2 Test Customer",
        "0481089573",
        "phase2-test@example.com",
        "1 Phase2 St, Perth WA",
        "2 Phase2 Ave, Perth WA",
        (date.today() + timedelta(days=3)).isoformat(),
        1,
        "Phase 2 automation test",
        hourly_rate=1.0,
        callout_fee=0.0,
        duration_hours="1",
        gst_enabled=1,
        payment_status="Unpaid",
        invoice_status="SENT",
    )
    db.update_booking_invoice_fields(
        bid,
        {
            "invoice_number": "INV-PHASE2-TEST",
            "invoice_issue_date": date.today().isoformat(),
            "invoice_due_date": (date.today() + timedelta(days=7)).isoformat(),
            "payment_status": "Unpaid",
            "invoice_status": "SENT",
            "paid_at": "",
            "xero_invoice_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "xero_payment_id": "",
            "stripe_checkout_session_id": "",
            "stripe_payment_intent_id": "",
            "stripe_payment_status": "",
            "sms_payment_confirmation_sent_at": "",
            "sms_payment_reminder_sent_at": "",
        },
    )
    results["booking_id"] = bid

    row = dict(db.get_booking(bid))
    with patch("integrations.sms.is_configured", return_value=True), patch(
        "integrations.sms.send_message", side_effect=_mock_sms_send
    ), patch.object(config, "SMS_ENABLED", True):
        ok, msg, url = create_checkout_session(
            row,
            success_url="http://127.0.0.1:5001/bookings/{0}/stripe/success?session_id={{CHECKOUT_SESSION_ID}}".format(
                bid
            ),
            cancel_url="http://127.0.0.1:5001/bookings/{0}/invoice/preview".format(bid),
        )
        results["checkout"] = {"ok": ok, "message": msg, "url": url or ""}

        session_id = (
            dict(db.get_booking(bid)).get("stripe_checkout_session_id")
            or "cs_test_phase2"
        )
        payload, header = _signed_webhook(session_id, bid)
        wh_ok, wh_msg = handle_webhook_event(payload, header)
        row = dict(db.get_booking(bid))
        results["webhook_first"] = {
            "ok": wh_ok,
            "message": wh_msg,
            "payment_status": row.get("payment_status"),
            "invoice_status": row.get("invoice_status"),
            "xero_payment_id": row.get("xero_payment_id") or "",
            "sms_payment_confirmation_sent_at": row.get(
                "sms_payment_confirmation_sent_at"
            )
            or "",
        }
        results["automation_after_webhook"] = _log_summary(_logs_for(bid))

        payload2, header2 = _signed_webhook(
            session_id,
            bid,
            event_id="evt_phase2_test_duplicate",
        )
        dup_ok, dup_msg = handle_webhook_event(payload2, header2)
        row_dup = dict(db.get_booking(bid))
        sms_logs = [
            e
            for e in _logs_for(bid)
            if e.get("automation_type") == automation.AUTOMATION_SMS_PAYMENT_CONFIRMATION
            and e.get("status") == automation.STATUS_SENT
        ]
        results["webhook_duplicate"] = {
            "ok": dup_ok,
            "message": dup_msg,
            "sms_sent_count": len(sms_logs),
            "sms_sent_at_unchanged": bool(
                row_dup.get("sms_payment_confirmation_sent_at")
            ),
        }

        from integrations.sms_automation import send_template_sms, run_payment_reminders

        rem_ok, rem_msg = send_template_sms(
            dict(db.get_booking(bid)), "payment_reminder", force=True
        )
        rem_dup_ok, rem_dup_msg = send_template_sms(
            dict(db.get_booking(bid)), "payment_reminder", force=False
        )
        results["payment_reminder_manual"] = {
            "first": {"ok": rem_ok, "message": rem_msg},
            "duplicate_blocked": {"ok": rem_dup_ok, "message": rem_dup_msg},
        }

        db.update_booking_status(bid, "Completed")
        prev = "Quote"
        booking = dict(db.get_booking(bid))
        review_automation.schedule_on_completed(booking, prev)

        conn = db.get_connection()
        conn.execute(
            "UPDATE review_requests SET scheduled_at = ? WHERE booking_id = ? AND status = ?",
            (
                (datetime.utcnow() - timedelta(minutes=1)).isoformat(timespec="seconds"),
                bid,
                automation.STATUS_SCHEDULED,
            ),
        )
        conn.commit()
        conn.close()

        review_msgs = review_automation.process_due_requests()
        review_dup = review_automation.process_due_requests()
        results["google_review"] = {
            "first_send": review_msgs,
            "duplicate_send": review_dup,
        }
        results["final_automation_log"] = _log_summary(_logs_for(bid))

    out_path = RESULTS_DIR / "phase2_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print("RESULTS_FILE", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
