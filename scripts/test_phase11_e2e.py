#!/usr/bin/env python3
"""Phase 11 E2E — full flow through Completed + Google review request."""

import hashlib
import hmac
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase11"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import automation
import database as db
import services
from integrations import (
    confirmed_automation,
    gmail_inbox,
    gmail_parser,
    on_route_automation,
    review_automation,
    review_config,
    sms_config,
    stripe_config,
    xero,
)
from integrations.stripe import (
    calculate_card_payment,
    create_checkout_session,
    handle_webhook_event,
)

PHASE9_LOG_TYPES = (
    "confirmation_sms_sent",
    "calendar_event_synced",
    "staff_notification_sent",
)
PHASE10_LOG_TYPES = ("on_route_started", "eta_sms_sent")
PHASE11_LOG_TYPES = (
    "review_request_scheduled",
    "review_request_sent",
    "review_request_cancelled",
)


def _sample_message() -> dict:
    body = (
        "Customer name: Phase11 E2E Customer\n"
        "Phone: 0412 345 678\n"
        "Email: phase11-e2e@example.com\n"
        "Move date: 30/08/2026\n"
        "Pickup: 10 Kings Park Rd, West Perth WA\n"
        "Delivery: 5 Swan St, Fremantle WA\n"
        "Bedrooms: 2\n"
        "Notes: Phase 11 end-to-end test booking.\n"
    )
    return {
        "payload": {
            "headers": [
                {
                    "name": "From",
                    "value": "Phase11 E2E Customer <phase11-e2e@example.com>",
                },
                {"name": "Subject", "value": "Moving quote — Phase 11 E2E"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {
                        "data": __import__("base64")
                        .urlsafe_b64encode(body.encode("utf-8"))
                        .decode("utf-8")
                        .rstrip("=")
                    },
                }
            ],
        }
    }


def _signed_webhook(
    session_id: str,
    booking_id: int,
    *,
    invoice_number: str,
    base_total: float,
    card_total: float,
    surcharge: float,
    payment_intent: str,
) -> tuple:
    whsec = stripe_config.get_webhook_secret()
    timestamp = int(time.time())
    event = {
        "id": "evt_phase11_e2e_{0}".format(int(time.time())),
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
                    "invoice_number": invoice_number,
                    "base_total": str(base_total),
                    "surcharge_amount": str(surcharge),
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
    return payload, "t={0},v1={1}".format(timestamp, sig)


def _logs_for(booking_id: int, automation_type: str) -> list:
    return [
        e
        for e in db.list_automation_logs(limit=100)
        if e.get("booking_id") == booking_id
        and e.get("automation_type") == automation_type
    ]


def _mock_sms_send(booking, body, **kwargs):
    return True, "SMS sent (Phase 11 mock)", "SMphase11_mock_sid"


def _mock_calendar_sync(booking):
    db.update_booking_integration_fields(
        int(booking["id"]),
        {"google_calendar_event_id": "evt_phase11_mock_{0}".format(booking["id"])},
    )
    return "Added to Google Calendar."


def _mock_staff_email(to, subject, body):
    return True, "Staff notification sent (Phase 11 mock)"


def _backdate_review_schedule(booking_id: int) -> None:
    past = (datetime.utcnow() - timedelta(minutes=1)).isoformat(timespec="seconds")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE review_requests SET scheduled_at = ? WHERE booking_id = ? AND status = ?",
            (past, booking_id, automation.STATUS_SCHEDULED),
        )
        conn.commit()


def main() -> int:
    db.init_db()
    results: dict = {"steps": []}

    if not xero.is_ready():
        results["blocked"] = "Connect Xero in Settings before running Phase 11 E2E."
        print(json.dumps(results, indent=2))
        print("FAIL")
        return 1

    if not stripe_config.get_webhook_secret():
        results["blocked"] = "Configure Stripe webhook secret before running Phase 11 E2E."
        print(json.dumps(results, indent=2))
        print("FAIL")
        return 1

    if not xero.payments_scope_granted():
        results["blocked"] = (
            "Re-connect Xero to grant accounting.payments scope before Phase 11 E2E."
        )
        print(json.dumps(results, indent=2))
        print("FAIL")
        return 1

    review_config.save_settings(
        automation_enabled=True,
        wait_hours=24,
        channel=review_config.CHANNEL_SMS_OR_EMAIL,
        google_review_url="https://g.page/r/phase11-e2e-test",
        sms_template=review_config.get_sms_template(),
        email_subject=review_config.get_email_subject(),
        email_body=review_config.get_email_body(),
    )

    raw = _sample_message()
    fields = gmail_parser.parse_gmail_message(raw)
    message_id = "msg_phase11_e2e_{0}".format(int(time.time()))
    ok, msg, booking_id = gmail_inbox.create_booking_from_email(message_id, fields)
    results["gmail_create"] = {"ok": ok, "message": msg, "booking_id": booking_id}
    results["steps"].append(msg)
    if not ok:
        _write_results(results)
        print("FAIL")
        return 1

    row = dict(db.get_booking(booking_id))
    db.update_booking(
        booking_id,
        row["customer_name"],
        row["phone"],
        row["email"],
        row["pickup_address"],
        row["delivery_address"],
        row["move_date"],
        row["num_movers"],
        row["notes"],
        start_time="09:30",
        finish_time=row.get("finish_time") or "",
        duration_hours="1",
        crew=row.get("crew") or "",
        hourly_rate=1.0,
        callout_fee=0.0,
        gst_enabled=row.get("gst_enabled") or 1,
        payment_status=row.get("payment_status") or "Unpaid",
        invoice_status=row.get("invoice_status") or "",
        status=row.get("status") or "Pending",
    )
    results["after_gmail"] = {"status": dict(db.get_booking(booking_id)).get("status")}

    sms_patches = (
        patch.object(sms_config, "is_automation_enabled", return_value=True),
        patch.object(sms_config, "is_trigger_enabled", return_value=True),
        patch("integrations.sms.is_configured", return_value=True),
        patch("integrations.sms.send_message", side_effect=_mock_sms_send),
    )

    db.update_booking_status(booking_id, "Confirmed")
    with sms_patches[0], sms_patches[1], sms_patches[2], sms_patches[3], patch(
        "integrations.google_calendar.sync_booking_to_calendar",
        side_effect=_mock_calendar_sync,
    ), patch("integrations.email_send.send_email", side_effect=_mock_staff_email), patch(
        "integrations.gmail_config.admin_notify_email",
        return_value="admin@japaneseremovals.test",
    ):
        services.after_booking_updated(booking_id, previous_status="Pending")

    row = dict(db.get_booking(booking_id))
    if not xero.is_real_invoice_id(row.get("xero_invoice_id")):
        _write_results(results)
        print("FAIL")
        return 1
    results["steps"].append("Pending → Confirmed automation")

    with sms_patches[0], sms_patches[1], sms_patches[2], sms_patches[3]:
        services.start_driver_on_route(
            booking_id,
            driver_name="Yasu",
            manual_eta_minutes=25,
        )
    results["steps"].append("On Route + ETA SMS")

    row = dict(db.get_booking(booking_id))
    ok, _checkout_msg, _url = create_checkout_session(
        row,
        success_url="http://127.0.0.1:5001/bookings/{0}/stripe/success?session_id={{CHECKOUT_SESSION_ID}}".format(
            booking_id
        ),
        cancel_url="http://127.0.0.1:5001/bookings/{0}/invoice/preview".format(booking_id),
    )
    row = dict(db.get_booking(booking_id))
    session_id = row.get("stripe_checkout_session_id") or "cs_phase11_e2e"
    calc = calculate_card_payment(1.0)
    invoice_number = row.get("invoice_number") or "PHASE11"
    payment_intent = "pi_phase11_e2e_{0}".format(int(time.time()))
    payload, header = _signed_webhook(
        session_id,
        booking_id,
        invoice_number=invoice_number,
        base_total=calc["base_total"],
        card_total=calc["card_total"],
        surcharge=calc["surcharge_amount"],
        payment_intent=payment_intent,
    )
    wh_ok, wh_msg = handle_webhook_event(payload, header)
    results["after_stripe"] = {
        "webhook_ok": wh_ok,
        "status": dict(db.get_booking(booking_id)).get("status"),
        "payment_status": dict(db.get_booking(booking_id)).get("payment_status"),
    }
    results["steps"].append("Stripe payment → Paid")
    if not wh_ok:
        _write_results(results)
        print("FAIL")
        return 1

    db.update_booking_status(booking_id, "Completed")
    completed_messages = services.after_booking_updated(
        booking_id, previous_status="Paid"
    )
    row = dict(db.get_booking(booking_id))
    phase11_scheduled_logs = _logs_for(booking_id, "review_request_scheduled")
    results["after_completed"] = {
        "status": row.get("status"),
        "completed_at": row.get("completed_at") or "",
        "review_request_scheduled_at": row.get("review_request_scheduled_at") or "",
        "integration_messages": completed_messages,
        "scheduled_log": phase11_scheduled_logs[0] if phase11_scheduled_logs else None,
    }
    results["steps"].append("Completed → review request scheduled")

    review_automation.schedule_on_completed(row, "Paid")
    dup_schedule_partial = len(
        [
            e
            for e in _logs_for(booking_id, "review_request_scheduled")
            if e.get("status") == automation.STATUS_PARTIAL
        ]
    )

    _backdate_review_schedule(booking_id)
    with sms_patches[0], sms_patches[1], sms_patches[2], sms_patches[3]:
        review_msgs = review_automation.process_due_requests()
    review_row = db.get_review_request_for_booking(booking_id)
    with sms_patches[0], sms_patches[1], sms_patches[2], sms_patches[3]:
        dup_send_ok, dup_send_msg = review_automation.send_review_request(
            dict(review_row)
        )

    row = dict(db.get_booking(booking_id))
    phase11_sent_logs = _logs_for(booking_id, "review_request_sent")
    results["after_review_send"] = {
        "process_messages": review_msgs,
        "duplicate_send_ok": dup_send_ok,
        "duplicate_send_message": dup_send_msg,
        "review_request_sent_at": row.get("review_request_sent_at") or "",
        "sent_log": phase11_sent_logs[0] if phase11_sent_logs else None,
        "sent_log_count": len(phase11_sent_logs),
        "partial_sent_logs": len(
            [e for e in phase11_sent_logs if e.get("status") == automation.STATUS_PARTIAL]
        ),
    }
    results["dedupe"] = {
        "schedule_partial_logs": dup_schedule_partial,
        "sent_log_count": len(phase11_sent_logs),
    }
    results["steps"].append("Review SMS sent after wait period")

    phase9_logs = {key: _logs_for(booking_id, key) for key in PHASE9_LOG_TYPES}
    phase10_logs = {key: _logs_for(booking_id, key) for key in PHASE10_LOG_TYPES}

    phase9_ok = all(
        phase9_logs[key] and phase9_logs[key][0].get("status") in ("sent", "success")
        for key in PHASE9_LOG_TYPES
    )
    phase10_ok = (
        phase10_logs["on_route_started"]
        and phase10_logs["eta_sms_sent"]
        and phase10_logs["eta_sms_sent"][0].get("status") == "sent"
    )
    phase11_ok = (
        row.get("status") == "Completed"
        and bool(row.get("completed_at"))
        and bool(row.get("review_request_scheduled_at"))
        and bool(row.get("review_request_sent_at"))
        and phase11_scheduled_logs
        and phase11_scheduled_logs[0].get("status") == automation.STATUS_SCHEDULED
        and any(
            e.get("status") == automation.STATUS_SENT for e in phase11_sent_logs
        )
        and dup_schedule_partial >= 1
        and results["after_review_send"]["partial_sent_logs"] >= 1
        and results["after_review_send"]["sent_log_count"] == 2
    )

    passed = (
        results["after_gmail"]["status"] == "Pending"
        and results["after_stripe"]["status"] == "Paid"
        and phase9_ok
        and phase10_ok
        and phase11_ok
    )
    results["phase9_automation_ok"] = phase9_ok
    results["phase10_automation_ok"] = phase10_ok
    results["phase11_automation_ok"] = phase11_ok
    results["passed"] = bool(passed)

    _write_results(results)
    print(json.dumps(results, indent=2))
    print("RESULTS_FILE", RESULTS_DIR / "phase11_results.json")
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


def _write_results(results: dict) -> None:
    out_path = RESULTS_DIR / "phase11_results.json"
    out_path.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
