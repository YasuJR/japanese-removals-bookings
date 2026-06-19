#!/usr/bin/env python3
"""Phase 8 E2E — Gmail → Pending → Confirmed → Invoice → Stripe → Paid."""

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase8"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
import services
from integrations import gmail_inbox, gmail_parser, sms_config, stripe_config, xero
from integrations.stripe import (
    calculate_card_payment,
    create_checkout_session,
    handle_webhook_event,
)


def _sample_message() -> dict:
    body = (
        "Customer name: Phase8 E2E Customer\n"
        "Phone: 0412 345 678\n"
        "Email: phase8-e2e@example.com\n"
        "Move date: 30/08/2026\n"
        "Pickup: 10 Kings Park Rd, West Perth WA\n"
        "Delivery: 5 Swan St, Fremantle WA\n"
        "Bedrooms: 2\n"
        "Notes: Phase 8 end-to-end test booking.\n"
    )
    return {
        "payload": {
            "headers": [
                {
                    "name": "From",
                    "value": "Phase8 E2E Customer <phase8-e2e@example.com>",
                },
                {"name": "Subject", "value": "Moving quote — Phase 8 E2E"},
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
        "id": "evt_phase8_e2e_{0}".format(int(time.time())),
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


def _payment_logs(booking_id: int) -> list:
    return [
        e
        for e in db.list_automation_logs(limit=30)
        if e.get("booking_id") == booking_id
        and e.get("automation_type") == "stripe_payment_received"
    ]


def main() -> int:
    db.init_db()
    results: dict = {"steps": []}

    if not xero.is_ready():
        results["blocked"] = "Connect Xero in Settings before running Phase 8 E2E."
        print(json.dumps(results, indent=2))
        print("FAIL")
        return 1

    if not stripe_config.get_webhook_secret():
        results["blocked"] = "Configure Stripe webhook secret before running Phase 8 E2E."
        print(json.dumps(results, indent=2))
        print("FAIL")
        return 1

    if not xero.payments_scope_granted():
        results["blocked"] = (
            "Re-connect Xero to grant accounting.payments scope before Phase 8 E2E."
        )
        print(json.dumps(results, indent=2))
        print("FAIL")
        return 1

    bank_code = stripe_config.xero_payment_account_code() or xero.default_bank_account_code()
    if bank_code and not stripe_config.xero_payment_account_configured():
        merged = stripe_config.settings_for_form()
        stripe_config.save_settings(
            stripe_enabled=stripe_config.is_enabled(),
            publishable_key=merged.get("publishable_key") or "",
            secret_key=merged.get("secret_key") or "",
            webhook_secret=stripe_config.get_webhook_secret(),
            card_surcharge_percent=stripe_config.surcharge_percent(),
            xero_payment_account_code=bank_code,
        )
        results["steps"].append("Set Stripe Xero payment account to {0}".format(bank_code))

    raw = _sample_message()
    fields = gmail_parser.parse_gmail_message(raw)
    message_id = "msg_phase8_e2e_{0}".format(int(time.time()))
    ok, msg, booking_id = gmail_inbox.create_booking_from_email(message_id, fields)
    results["gmail_create"] = {"ok": ok, "message": msg, "booking_id": booking_id}
    results["steps"].append(msg)
    if not ok:
        out_path = RESULTS_DIR / "phase8_results.json"
        out_path.write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2))
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
        start_time=row.get("start_time") or "",
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
    results["after_gmail"] = {
        "status": dict(db.get_booking(booking_id)).get("status"),
        "hourly_rate": 1.0,
    }

    db.update_booking_status(booking_id, "Confirmed")
    with patch.object(sms_config, "is_automation_enabled", return_value=False):
        integration_messages = services.after_booking_updated(
            booking_id, previous_status="Pending"
        )
    row = dict(db.get_booking(booking_id))
    inv = (
        xero.fetch_invoice(row.get("xero_invoice_id") or "")
        if xero.is_real_invoice_id(row.get("xero_invoice_id"))
        else None
    )
    results["after_confirmed"] = {
        "status": row.get("status"),
        "invoice_number": row.get("invoice_number") or "",
        "xero_invoice_id": row.get("xero_invoice_id") or "",
        "invoice_status": row.get("invoice_status") or "",
        "xero_status": (inv or {}).get("Status") or "",
        "integration_messages": integration_messages,
    }
    if not xero.is_real_invoice_id(row.get("xero_invoice_id")):
        out_path = RESULTS_DIR / "phase8_results.json"
        out_path.write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2))
        print("FAIL")
        return 1

    ok, checkout_msg, _url = create_checkout_session(
        row,
        success_url="http://127.0.0.1:5001/bookings/{0}/stripe/success?session_id={{CHECKOUT_SESSION_ID}}".format(
            booking_id
        ),
        cancel_url="http://127.0.0.1:5001/bookings/{0}/invoice/preview".format(booking_id),
    )
    row = dict(db.get_booking(booking_id))
    session_id = row.get("stripe_checkout_session_id") or "cs_phase8_e2e"
    calc = calculate_card_payment(1.0)
    invoice_number = row.get("invoice_number") or "PHASE8"
    payment_intent = "pi_phase8_e2e_{0}".format(int(time.time()))
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
    row = dict(db.get_booking(booking_id))
    inv = (
        xero.fetch_invoice(row.get("xero_invoice_id") or "")
        if xero.is_real_invoice_id(row.get("xero_invoice_id"))
        else None
    )
    payment_logs = _payment_logs(booking_id)
    results["after_stripe"] = {
        "checkout_ok": ok,
        "checkout_message": checkout_msg,
        "webhook_ok": wh_ok,
        "webhook_message": wh_msg,
        "status": row.get("status"),
        "payment_status": row.get("payment_status"),
        "invoice_status": row.get("invoice_status"),
        "paid_at": row.get("paid_at") or "",
        "xero_payment_id": row.get("xero_payment_id") or "",
        "xero_status": (inv or {}).get("Status") or "",
        "xero_amount_due": float((inv or {}).get("AmountDue") or -1),
        "stripe_payment_received_log": payment_logs[0] if payment_logs else None,
    }

    xero_payment_id_after_first = row.get("xero_payment_id") or ""
    payload2, header2 = _signed_webhook(
        session_id,
        booking_id,
        invoice_number=invoice_number,
        base_total=calc["base_total"],
        card_total=calc["card_total"],
        surcharge=calc["surcharge_amount"],
        payment_intent=payment_intent,
    )
    wh_ok2, wh_msg2 = handle_webhook_event(payload2, header2)
    row_after_dup = dict(db.get_booking(booking_id))
    dup_logs = _payment_logs(booking_id)
    duplicate_log = next(
        (e for e in dup_logs if e.get("status") == "partial"),
        None,
    )
    results["duplicate_webhook"] = {
        "webhook_ok": wh_ok2,
        "webhook_message": wh_msg2,
        "xero_payment_id_unchanged": (
            (row_after_dup.get("xero_payment_id") or "") == xero_payment_id_after_first
        ),
        "duplicate_log": duplicate_log,
        "payment_log_count": len(dup_logs),
    }

    passed = (
        results["after_gmail"]["status"] == "Pending"
        and results["after_confirmed"]["status"] == "Confirmed"
        and xero.is_real_invoice_id(results["after_confirmed"]["xero_invoice_id"])
        and wh_ok
        and row.get("status") == "Paid"
        and row.get("payment_status") == "Paid"
        and (row.get("invoice_status") or "").upper() == "PAID"
        and bool(row.get("xero_payment_id"))
        and results["after_stripe"]["xero_amount_due"] <= 0.01
        and payment_logs
        and payment_logs[0].get("status") == "success"
        and wh_ok2
        and results["duplicate_webhook"]["xero_payment_id_unchanged"]
        and duplicate_log is not None
    )
    results["passed"] = passed

    out_path = RESULTS_DIR / "phase8_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print("RESULTS_FILE", out_path)
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
