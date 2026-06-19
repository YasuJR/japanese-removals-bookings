#!/usr/bin/env python3
"""Phase 21 E2E — SMS inbound to booking or draft lead."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase21"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
import job_status
import sms_inbound_parser
from integrations import sms_inbound


def test_a_high_confidence_booking() -> dict:
    db.init_db()
    message = (
        "Hi I'm John Smith. Move date: 15/07/2026. "
        "Pickup: Subiaco to Delivery: Fremantle. Piano yes."
    )
    fields = sms_inbound_parser.parse_inbound_sms("+61412345678", message)
    failed = []
    if not sms_inbound_parser.meets_booking_threshold(fields):
        failed.append("Expected confidence >= 80%, got {0}".format(fields["confidence"]))
    if fields.get("customer_name") != "John Smith":
        failed.append("Name not parsed: {0}".format(fields.get("customer_name")))
    with patch(
        "integrations.sms_inbound.email_send.send_email",
        return_value=(True, "Sent"),
    ):
        result = sms_inbound.process_inbound_sms("+61412345678", message)
    if result.get("kind") != "booking":
        failed.append("Expected booking, got {0}".format(result.get("kind")))
    booking_id = int(result.get("booking_id") or 0)
    row = dict(db.get_booking(booking_id))
    if job_status.display(row) != "Pending":
        failed.append("Status should be Pending.")
    if (row.get("source") or "").strip() != "SMS":
        failed.append("Source should be SMS.")
    return {
        "name": "Test A — High confidence creates Pending SMS booking",
        "pass": not failed,
        "details": failed or ["Pending booking created from SMS."],
    }


def test_b_low_confidence_lead() -> dict:
    message = "Hello need a mover please call me"
    fields = sms_inbound_parser.parse_inbound_sms("+61400999888", message)
    failed = []
    if sms_inbound_parser.meets_booking_threshold(fields):
        failed.append("Expected confidence under 80%, got {0}".format(fields["confidence"]))
    with patch(
        "integrations.sms_inbound.email_send.send_email",
        return_value=(True, "Sent"),
    ):
        result = sms_inbound.process_inbound_sms("+61400999888", message)
    if result.get("kind") != "lead":
        failed.append("Expected draft lead, got {0}".format(result.get("kind")))
    lead_id = int(result.get("lead_id") or 0)
    lead = dict(db.get_draft_lead(lead_id))
    if (lead.get("source") or "").strip() != "SMS":
        failed.append("Lead source should be SMS.")
    if (lead.get("status") or "").strip() != "draft":
        failed.append("Lead status should be draft.")
    return {
        "name": "Test B — Low confidence creates draft lead",
        "pass": not failed,
        "details": failed or ["Draft lead saved for low-confidence SMS."],
    }


def test_c_convert_lead() -> dict:
    lead_id = db.create_draft_lead(
        customer_name="Lead Tester",
        phone="0411222333",
        move_date="2026-08-01",
        pickup_address="Perth, WA",
        delivery_address="Joondalup, WA",
        notes="Convert me",
        source="SMS",
        confidence=55.0,
        raw_message="Need move Perth to Joondalup",
    )
    ok, msg, booking_id = sms_inbound.convert_lead_to_booking(lead_id)
    lead = dict(db.get_draft_lead(lead_id))
    failed = []
    if not ok or not booking_id:
        failed.append("Convert failed: {0}".format(msg))
    if (lead.get("status") or "").strip() != "converted":
        failed.append("Lead should be marked converted.")
    row = dict(db.get_booking(booking_id))
    if (row.get("source") or "").strip() != "SMS":
        failed.append("Converted booking source should be SMS.")
    return {
        "name": "Test C — Convert lead to booking",
        "pass": not failed,
        "details": failed or ["Lead converted to Pending booking."],
    }


def test_d_reply_template() -> dict:
    lead = {
        "customer_name": "Sam",
        "move_date": "2026-09-01",
    }
    text = sms_inbound.reply_template_for_lead(lead)
    failed = []
    if "Sam" not in text:
        failed.append("Reply template should include customer name.")
    if "Japanese Removals" not in text:
        failed.append("Reply template should include company name.")
    return {
        "name": "Test D — Reply template",
        "pass": not failed,
        "details": failed or ["Reply template generated."],
    }


def test_e_inbound_webhook() -> dict:
    from app import app

    client = app.test_client()
    with patch(
        "integrations.sms_inbound.process_inbound_sms",
        return_value={"kind": "lead", "lead_id": 1, "confidence": 50},
    ) as mock_process:
        response = client.post(
            "/integrations/twilio/inbound",
            data={"From": "+61400111222", "Body": "Test inbound"},
        )
    failed = []
    if response.status_code != 200:
        failed.append("Webhook returned {0}".format(response.status_code))
    if b"<Response>" not in response.data:
        failed.append("Expected TwiML response.")
    if not mock_process.called:
        failed.append("Inbound handler not invoked.")
    return {
        "name": "Test E — Twilio inbound webhook",
        "pass": not failed,
        "details": failed or ["Inbound webhook accepts POST."],
    }


def main() -> int:
    db.init_db()
    results = [
        test_a_high_confidence_booking(),
        test_b_low_confidence_lead(),
        test_c_convert_lead(),
        test_d_reply_template(),
        test_e_inbound_webhook(),
    ]
    payload = {
        "phase": 21,
        "feature": "sms_inbound",
        "results": results,
        "all_pass": all(r["pass"] for r in results),
    }
    out_path = RESULTS_DIR / "phase21_results.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nPhase 21 — SMS Inbound E2E\n")
    print("| Test | Result |")
    print("|------|--------|")
    for row in results:
        label = row["name"].split(" — ", 1)[0]
        status = "PASS" if row["pass"] else "FAIL"
        print("| {0} | **{1}** |".format(label, status))
        for detail in row["details"]:
            print("  - {0}".format(detail))
    print("\nOverall: {0}".format("PASS" if payload["all_pass"] else "FAIL"))
    print("Results saved to {0}".format(out_path))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
