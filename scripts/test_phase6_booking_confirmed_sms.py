#!/usr/bin/env python3
"""Phase 6 — Pending → Confirmed SMS automation test."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase6"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
import services
from integrations import sms_automation, sms_config


def _mock_sms_send(booking, body, **kwargs):
    return True, "SMS sent (test mock)", "SMphase6_mock_sid"


def main() -> int:
    db.init_db()
    results = {"steps": []}

    body = sms_automation.render_template(
        {
            "id": 99,
            "customer_name": "Jane Smith",
            "phone": "0412345678",
            "move_date": "2026-08-15",
            "pickup_address": "1 Test St",
            "delivery_address": "2 Demo Ave",
        },
        "booking_confirmed",
    )
    results["template_preview"] = body
    results["steps"].append("Rendered booking_confirmed template")

    bid = db.create_booking(
        "Phase6 Test Customer",
        "0412999888",
        "phase6@example.com",
        "10 Test St, Perth",
        "20 Demo Ave, Fremantle",
        (date.today() + timedelta(days=14)).isoformat(),
        2,
        "Phase 6 SMS test",
        status="Pending",
    )
    results["booking_id"] = bid

    with patch.object(sms_config, "is_automation_enabled", return_value=True), patch.object(
        sms_config, "is_trigger_enabled", return_value=True
    ), patch("integrations.sms.is_configured", return_value=True), patch(
        "integrations.sms.send_message", side_effect=_mock_sms_send
    ):
        db.update_booking_status(bid, "Confirmed")
        messages = services.after_booking_updated(bid, previous_status="Pending")

    row = dict(db.get_booking(bid))
    logs = [
        e
        for e in db.list_automation_logs(limit=10)
        if e.get("booking_id") == bid
        and e.get("automation_type") == "confirmation_sms_sent"
    ]
    results["after_confirmed"] = {
        "status": row.get("status"),
        "sms_booking_confirmed_sent_at": row.get("sms_booking_confirmed_sent_at"),
        "integration_messages": messages,
        "automation_log": logs[0] if logs else None,
    }

    passed = (
        row.get("status") == "Confirmed"
        and bool(row.get("sms_booking_confirmed_sent_at"))
        and logs
        and logs[0].get("status") == "sent"
        and "Hi Jane," in body
        and "Japanese Removals" in body
        and "8:00 AM" in body
    )
    results["passed"] = passed

    out_path = RESULTS_DIR / "phase6_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print("RESULTS_FILE", out_path)
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
