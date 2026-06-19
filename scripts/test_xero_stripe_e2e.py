#!/usr/bin/env python3
"""End-to-end: booking create → Xero invoice → Stripe webhook → Xero payment."""

import json
import hmac
import hashlib
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "xero_stripe_e2e"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
import services
from integrations import stripe_config, xero
from integrations.stripe import create_checkout_session, handle_webhook_event


def _signed_webhook(session_id: str, booking_id: int, card_total: float = 1.02) -> tuple:
    whsec = stripe_config.get_webhook_secret()
    timestamp = int(time.time())
    base = round(card_total - 0.02, 2)
    event = {
        "id": "evt_xero_stripe_e2e",
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
                    "invoice_number": "E2E-TEST",
                    "base_total": str(base),
                    "surcharge_amount": "0.02",
                    "card_total": str(card_total),
                },
                "payment_intent": "pi_xero_stripe_e2e",
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


def main() -> int:
    db.init_db()
    results: dict = {"steps": []}
    results["xero_payments_scope"] = xero.payments_scope_granted()
    if not xero.payments_scope_granted():
        results["blocked"] = (
            "Re-connect Xero in Settings → Xero to grant accounting.payments scope."
        )
        print(results["blocked"])

    if not xero.is_ready():
        print("SKIP: Xero not connected")
        return 1

    bank_code = stripe_config.xero_payment_account_code() or xero.default_bank_account_code()
    results["bank_account_code"] = bank_code
    if not bank_code:
        print("FAIL: No Xero bank account code available")
        return 1
    if not stripe_config.xero_payment_account_configured():
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

    bid = db.create_booking(
        "Xero Stripe E2E Customer",
        "0481089573",
        "xero-stripe-e2e@example.com",
        "1 E2E St, Perth WA",
        "2 E2E Ave, Perth WA",
        (date.today() + timedelta(days=5)).isoformat(),
        1,
        "Xero + Stripe E2E test",
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
            "invoice_number": "",
            "invoice_issue_date": date.today().isoformat(),
            "invoice_due_date": (date.today() + timedelta(days=7)).isoformat(),
        },
    )
    results["booking_id"] = bid

    create_msgs = []
    auto_msg = services._auto_create_xero_invoice_on_create(bid)
    if auto_msg:
        create_msgs.append(auto_msg)
    results["after_booking_created"] = create_msgs
    row = dict(db.get_booking(bid))
    results["after_create"] = {
        "xero_invoice_id": row.get("xero_invoice_id") or "",
        "invoice_number": row.get("invoice_number") or "",
        "invoice_status": row.get("invoice_status") or "",
    }
    if not xero.is_real_invoice_id(row.get("xero_invoice_id")):
        print("FAIL: Xero invoice not created on booking create")
        print(json.dumps(results, indent=2))
        return 1
    if not (row.get("invoice_number") or "").strip():
        print("FAIL: Xero invoice number not saved on booking")
        print(json.dumps(results, indent=2))
        return 1

    ok, msg, url = create_checkout_session(
        row,
        success_url="http://127.0.0.1:5001/bookings/{0}/stripe/success?session_id={{CHECKOUT_SESSION_ID}}".format(
            bid
        ),
        cancel_url="http://127.0.0.1:5001/bookings/{0}/invoice/preview".format(bid),
    )
    results["checkout"] = {"ok": ok, "message": msg, "url": (url or "")[:80]}
    session_id = dict(db.get_booking(bid)).get("stripe_checkout_session_id") or "cs_e2e_test"
    payload, header = _signed_webhook(session_id, bid)
    wh_ok, wh_msg = handle_webhook_event(payload, header)
    row = dict(db.get_booking(bid))
    inv = xero.fetch_invoice(row.get("xero_invoice_id") or "") if row.get("xero_invoice_id") else None
    results["after_webhook"] = {
        "webhook_ok": wh_ok,
        "webhook_message": wh_msg,
        "payment_status": row.get("payment_status"),
        "invoice_status": row.get("invoice_status"),
        "invoice_number": row.get("invoice_number") or "",
        "paid_at": row.get("paid_at") or "",
        "xero_payment_id": row.get("xero_payment_id") or "",
        "xero_amount_due": float((inv or {}).get("AmountDue") or -1),
        "xero_amount_paid": float((inv or {}).get("AmountPaid") or 0),
        "xero_status": (inv or {}).get("Status") or "",
        "xero_fully_paid_on": (inv or {}).get("FullyPaidOnDate") or "",
    }

    passed = (
        wh_ok
        and row.get("payment_status") == "Paid"
        and (row.get("invoice_status") or "").upper() == "PAID"
        and bool(row.get("xero_payment_id"))
        and bool(row.get("invoice_number"))
        and results["after_webhook"]["xero_amount_due"] <= 0.01
    )
    results["passed"] = passed

    out_path = RESULTS_DIR / "e2e_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print("RESULTS_FILE", out_path)
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
