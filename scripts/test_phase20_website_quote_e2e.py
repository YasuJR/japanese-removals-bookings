#!/usr/bin/env python3
"""Phase 20 E2E — Website quote form."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase20"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import database as db
import job_status
import quote_form
import services
from integrations import website_quote


def _valid_form(**overrides):
    base = {
        "name": "Website Tester",
        "phone": "0412345678",
        "email": "website.tester@example.com",
        "move_date": (date.today() + timedelta(days=14)).isoformat(),
        "pickup_suburb": "Subiaco",
        "delivery_suburb": "Fremantle",
        "bedrooms": "2",
        "stairs": "no",
        "piano": "no",
        "pool_table": "no",
        "packing_required": "yes",
        "notes": "Phase 20 test quote",
        quote_form.HONEYPOT_FIELD: "",
    }
    base.update(overrides)
    return base


def test_a_validation() -> dict:
    data, errors, spam = quote_form.parse_quote_form({"name": "", "email": "bad"}, "127.0.0.1")
    failed = []
    if spam:
        failed.append("Should not be spam blocked.")
    if not errors:
        failed.append("Expected validation errors.")
    return {
        "name": "Test A — Basic validation",
        "pass": not failed,
        "details": failed or ["Validation rejects empty/invalid input."],
    }


def test_b_honeypot() -> dict:
    form = _valid_form(**{quote_form.HONEYPOT_FIELD: "http://spam.example"})
    data, errors, spam = quote_form.parse_quote_form(form, "127.0.0.2")
    failed = []
    if not spam:
        failed.append("Honeypot should block submission.")
    if errors:
        failed.append("Honeypot should not surface errors.")
    if data:
        failed.append("Honeypot should not return booking data.")
    return {
        "name": "Test B — Honeypot anti-spam",
        "pass": not failed,
        "details": failed or ["Honeypot silently blocks bots."],
    }


@patch("integrations.website_quote.email_send.send_email", return_value=(True, "Sent"))
def test_c_create_pending(mock_send) -> dict:
    db.init_db()
    form = _valid_form()
    data, errors, spam = quote_form.parse_quote_form(form, "127.0.0.3")
    failed = []
    if errors or spam:
        failed.append("Valid form rejected: {0}".format(errors))
        return {
            "name": "Test C — Pending booking with source Website",
            "pass": False,
            "details": failed,
        }

    ok, _msg, booking_id, _ = website_quote.submit_website_quote(data, "127.0.0.3")
    row = dict(db.get_booking(booking_id))
    integration = db.get_booking(booking_id)
    booking = dict(integration)

    if not ok:
        failed.append("Submit failed.")
    if job_status.display(row) != "Pending":
        failed.append("Status should be Pending.")
    if (booking.get("source") or "").strip() != "Website":
        failed.append("Source should be Website.")
    if (booking.get("google_calendar_event_id") or "").strip():
        failed.append("Calendar should not sync on website quote.")
    calendar_msgs = services.after_booking_created(booking_id)
    if calendar_msgs:
        failed.append("after_booking_created should skip Pending bookings.")
    if not mock_send.called:
        failed.append("Admin notification email not attempted.")
    return {
        "name": "Test C — Pending booking with source Website",
        "pass": not failed,
        "details": failed or ["Pending Website booking created without calendar."],
        "booking_id": booking_id,
    }


def test_d_rate_limit() -> dict:
    ip = "127.0.0.99"
    failed = []
    for i in range(quote_form.RATE_LIMIT_MAX):
        db.record_quote_submission(ip)
    _, errors, spam = quote_form.parse_quote_form(_valid_form(email="rate{0}@example.com".format(i)), ip)
    if not spam and not errors:
        failed.append("Rate limit should block further submissions.")
    return {
        "name": "Test D — Rate limit",
        "pass": not failed,
        "details": failed or ["Rate limit enforced."],
    }


def test_e_public_route() -> dict:
    from app import app

    client = app.test_client()
    response = client.get("/quote")
    failed = []
    if response.status_code != 200:
        failed.append("GET /quote returned {0}".format(response.status_code))
    body = response.get_data(as_text=True)
    if "Get a quote" not in body:
        failed.append("Quote page content missing.")
    if quote_form.HONEYPOT_FIELD not in body:
        failed.append("Honeypot field not rendered.")
    return {
        "name": "Test E — Public /quote page",
        "pass": not failed,
        "details": failed or ["Public quote page loads."],
    }


def main() -> int:
    db.init_db()
    results = [
        test_a_validation(),
        test_b_honeypot(),
        test_c_create_pending(),
        test_d_rate_limit(),
        test_e_public_route(),
    ]
    payload = {
        "phase": 20,
        "feature": "website_quote_form",
        "results": results,
        "all_pass": all(r["pass"] for r in results),
    }
    out_path = RESULTS_DIR / "phase20_results.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nPhase 20 — Website Quote Form E2E\n")
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
