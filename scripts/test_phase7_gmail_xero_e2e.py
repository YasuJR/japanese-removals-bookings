#!/usr/bin/env python3
"""Phase 7 E2E — Gmail email → Pending booking → Confirmed → Xero invoice."""

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase7"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
import services
from integrations import gmail_inbox, gmail_parser, sms_config, xero


def _sample_message() -> dict:
    body = (
        "Customer name: Phase7 E2E Customer\n"
        "Phone: 0412 345 678\n"
        "Email: phase7-e2e@example.com\n"
        "Move date: 25/08/2026\n"
        "Pickup: 10 Kings Park Rd, West Perth WA\n"
        "Delivery: 5 Swan St, Fremantle WA\n"
        "Bedrooms: 2\n"
        "Notes: Phase 7 end-to-end test booking.\n"
    )
    return {
        "payload": {
            "headers": [
                {
                    "name": "From",
                    "value": "Phase7 E2E Customer <phase7-e2e@example.com>",
                },
                {"name": "Subject", "value": "Moving quote — Phase 7 E2E"},
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


def main() -> int:
    db.init_db()
    results: dict = {"steps": []}

    if not xero.is_ready():
        results["blocked"] = "Connect Xero in Settings before running Phase 7 E2E."
        print(json.dumps(results, indent=2))
        print("FAIL")
        return 1

    raw = _sample_message()
    fields = gmail_parser.parse_gmail_message(raw)
    message_id = "msg_phase7_e2e_{0}".format(int(time.time()))
    ok, msg, booking_id = gmail_inbox.create_booking_from_email(message_id, fields)
    results["gmail_create"] = {"ok": ok, "message": msg, "booking_id": booking_id}
    results["steps"].append(msg)
    if not ok:
        out_path = RESULTS_DIR / "phase7_results.json"
        out_path.write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2))
        print("FAIL")
        return 1

    row = dict(db.get_booking(booking_id))
    results["after_gmail"] = {
        "status": row.get("status"),
        "customer_name": row.get("customer_name"),
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
    logs = [
        e
        for e in db.list_automation_logs(limit=20)
        if e.get("booking_id") == booking_id
        and e.get("automation_type") == "xero_invoice_auto_create"
    ]

    results["after_confirmed"] = {
        "status": row.get("status"),
        "invoice_number": row.get("invoice_number") or "",
        "xero_invoice_id": row.get("xero_invoice_id") or "",
        "invoice_status": row.get("invoice_status") or "",
        "xero_automation_error": row.get("xero_invoice_automation_error") or "",
        "xero_status": (inv or {}).get("Status") or "",
        "xero_amount_due": float((inv or {}).get("AmountDue") or -1),
        "integration_messages": integration_messages,
        "automation_log": logs[0] if logs else None,
    }

    passed = (
        row.get("status") == "Confirmed"
        and xero.is_real_invoice_id(row.get("xero_invoice_id"))
        and bool((row.get("invoice_number") or "").strip())
        and (row.get("invoice_status") or "").upper() in ("AUTHORISED", "SENT", "PAID")
        and not (row.get("xero_invoice_automation_error") or "").strip()
        and logs
        and logs[0].get("status") == "success"
    )
    results["passed"] = passed

    out_path = RESULTS_DIR / "phase7_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print("RESULTS_FILE", out_path)
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
