#!/usr/bin/env python3
"""Test Gmail parser and Pending booking creation (no live Gmail API)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "gmail_inbox"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
import job_status
from integrations import gmail_inbox, gmail_parser


def _sample_message() -> dict:
    body = (
        "Customer name: Jane Smith\n"
        "Phone: 0412 345 678\n"
        "Email: jane.smith@example.com\n"
        "Move date: 20/07/2026\n"
        "Pickup: 10 Kings Park Rd, West Perth WA\n"
        "Delivery: 5 Swan St, Fremantle WA\n"
        "Bedrooms: 2\n"
        "Stairs: 1 flight at pickup, lift at delivery\n"
        "Piano: yes (upright)\n"
        "Pool table: no\n"
        "Packing required: yes\n"
        "Estimated volume: 25 m³\n"
        "Notes: Please call before arriving.\n"
    )
    return {
        "id": "msg_phase5_test_sample",
        "payload": {
            "headers": [
                {"name": "From", "value": "Jane Smith <jane.smith@example.com>"},
                {"name": "Subject", "value": "Moving quote request"},
            ],
            "mimeType": "text/plain",
            "body": {"data": ""},
            "parts": [],
        },
        "_test_body": body,
    }


def _plain_text_override(message: dict) -> dict:
    message = dict(message)
    body = message.pop("_test_body", "")
    message["payload"] = dict(message.get("payload") or {})
    message["payload"]["parts"] = [
        {
            "mimeType": "text/plain",
            "body": {
                "data": __import__("base64")
                .urlsafe_b64encode(body.encode("utf-8"))
                .decode("utf-8")
                .rstrip("=")
            },
        }
    ]
    return message


def main() -> int:
    db.init_db()
    results = {"steps": []}

    raw = _plain_text_override(_sample_message())
    fields = gmail_parser.parse_gmail_message(raw)
    results["parsed"] = fields
    results["steps"].append("Parsed sample enquiry email")

    missing = gmail_parser.missing_required_fields(fields)
    results["missing_required"] = missing

    message_id = "msg_phase5_test_{0}".format(int(__import__("time").time()))
    if db.is_gmail_message_processed(message_id):
        message_id = message_id + "_b"

    ok, msg, booking_id = gmail_inbox.create_booking_from_email(message_id, fields)
    results["create"] = {"ok": ok, "message": msg, "booking_id": booking_id}
    results["steps"].append(msg)

    row = dict(db.get_booking(booking_id)) if booking_id else {}
    results["booking"] = {
        "status": row.get("status"),
        "customer_name": row.get("customer_name"),
        "phone": row.get("phone"),
        "email": row.get("email"),
        "move_date": row.get("move_date"),
        "pickup_address": row.get("pickup_address"),
        "delivery_address": row.get("delivery_address"),
        "gmail_message_id": row.get("gmail_message_id"),
        "notes": row.get("notes"),
    }

    notes = row.get("notes") or ""
    passed = (
        ok
        and booking_id
        and row.get("status") == "Pending"
        and row.get("customer_name") == "Jane Smith"
        and row.get("pickup_address")
        and row.get("delivery_address")
        and row.get("move_date") == "2026-07-20"
        and not missing
        and job_status.normalize("Pending") == "Pending"
        and fields.get("bedrooms") == "2 bedrooms"
        and fields.get("estimated_volume") == "25 m³"
        and fields.get("packing_required") == "Yes"
        and fields.get("pool_table") == "No"
        and "Move details (from email):" in notes
        and "Bedrooms: 2 bedrooms" in notes
        and "Piano:" in notes
        and "Estimated volume: 25 m³" in notes
    )
    results["passed"] = passed

    out_path = RESULTS_DIR / "phase5_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print("RESULTS_FILE", out_path)
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
