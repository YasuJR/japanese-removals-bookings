#!/usr/bin/env python3
"""Phase 16 E2E — unpaid invoice payment reminder automation."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "phase16"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

import automation
import database as db
import invoice
import services
from integrations import payment_reminder_automation, sms_config


def _mock_sms_send(booking, body, **kwargs):
    return True, "SMS sent (test mock) to {0}".format(booking.get("phone")), "SMtest_phase16"


def _logs(booking_id: int, automation_type: str) -> list:
    return [
        e
        for e in db.list_automation_logs(limit=100)
        if e.get("booking_id") == booking_id
        and e.get("automation_type") == automation_type
    ]


def _create_unpaid_booking(
    label: str,
    *,
    status: str = "Completed",
    issue_days_ago: int = 3,
    invoice_status: str = "AUTHORISED",
) -> int:
    issue_date = (date.today() - timedelta(days=issue_days_ago)).isoformat()
    booking_id = db.create_booking(
        "{0} Customer".format(label),
        "0412000444",
        "{0}@example.com".format(label.lower().replace(" ", "")),
        "10 Reminder St, Perth WA",
        "20 Invoice Ave, Fremantle WA",
        date.today().isoformat(),
        2,
        "Phase 16 {0}".format(label),
        start_time="09:00",
        finish_time="12:00",
        duration_hours="3",
        hourly_rate=110.0,
        callout_fee=0.0,
        gst_enabled=1,
        payment_status=invoice.PAYMENT_STATUS_UNPAID,
        status=status,
    )
    db.update_booking_invoice_fields(
        booking_id,
        {
            "invoice_number": "INV-P16-{0}".format(booking_id),
            "invoice_status": invoice_status,
            "invoice_issue_date": issue_date,
            "payment_status": invoice.PAYMENT_STATUS_UNPAID,
        },
    )
    if status != "Pending":
        db.update_booking_status(booking_id, status)
    return booking_id


def _enable_automation():
    sms_config.save_settings(
        True,
        {key: True for key in sms_config.TEMPLATE_KEYS},
        {},
    )


@contextmanager
def _automation_patches():
    with patch("integrations.sms.is_configured", return_value=True), patch(
        "integrations.sms_config.is_automation_enabled", return_value=True
    ), patch("integrations.sms_config.is_trigger_enabled", return_value=True), patch(
        "integrations.sms.send_message", side_effect=_mock_sms_send
    ):
        yield


def test_unpaid_sends_reminder() -> dict:
    booking_id = _create_unpaid_booking("UnpaidA", issue_days_ago=3)
    with _automation_patches():
        messages = payment_reminder_automation.process_due_reminders()
    row = dict(db.get_booking(booking_id))
    failed = []
    if not (row.get("payment_reminder_1_sent_at") or "").strip():
        failed.append("payment_reminder_1_sent_at not set.")
    sent_logs = _logs(booking_id, automation.AUTOMATION_PAYMENT_REMINDER_1_SENT)
    if not any(l.get("status") == automation.STATUS_SENT for l in sent_logs):
        failed.append("No payment_reminder_1_sent log with status sent.")
    if not any(str(booking_id) in m for m in messages):
        failed.append("process_due_reminders did not report booking.")
    return {
        "name": "Test A — Unpaid invoice sends reminder after 3 days",
        "pass": not failed,
        "details": failed or ["Reminder 1 sent for unpaid authorised invoice."],
        "booking_id": booking_id,
    }


def test_paid_skips_reminder() -> dict:
    booking_id = _create_unpaid_booking("PaidB", issue_days_ago=7)
    invoice.set_payment_status(booking_id, True)
    with _automation_patches():
        payment_reminder_automation.process_due_reminders()
    row = dict(db.get_booking(booking_id))
    failed = []
    if (row.get("payment_reminder_1_sent_at") or "").strip():
        failed.append("Reminder sent despite Paid payment_status.")
    if _logs(booking_id, automation.AUTOMATION_PAYMENT_REMINDER_1_SENT):
        failed.append("Unexpected payment reminder log for paid booking.")
    return {
        "name": "Test B — Paid invoice skips reminder",
        "pass": not failed,
        "details": failed or ["Paid booking skipped by reminder automation."],
        "booking_id": booking_id,
    }


def test_cancelled_skips_reminder() -> dict:
    booking_id = _create_unpaid_booking("CancelledC", issue_days_ago=7)
    db.update_booking_status(booking_id, "Cancelled")
    with _automation_patches():
        payment_reminder_automation.process_due_reminders()
    row = dict(db.get_booking(booking_id))
    failed = []
    if (row.get("payment_reminder_1_sent_at") or "").strip():
        failed.append("Reminder sent for cancelled booking.")
    return {
        "name": "Test C — Cancelled booking skips reminder",
        "pass": not failed,
        "details": failed or ["Cancelled booking skipped."],
        "booking_id": booking_id,
    }


def test_duplicate_blocked() -> dict:
    booking_id = _create_unpaid_booking("DuplicateD", issue_days_ago=3)
    with _automation_patches():
        payment_reminder_automation.process_due_reminders()
        payment_reminder_automation.process_due_reminders()
    sent_logs = [
        l
        for l in _logs(booking_id, automation.AUTOMATION_PAYMENT_REMINDER_1_SENT)
        if l.get("status") == automation.STATUS_SENT
    ]
    failed = []
    if len(sent_logs) != 1:
        failed.append(
            "Expected exactly one sent log, found {0}.".format(len(sent_logs))
        )
    return {
        "name": "Test D — Duplicate reminders blocked",
        "pass": not failed,
        "details": failed or ["Second run did not send duplicate reminder 1."],
        "booking_id": booking_id,
    }


def test_manual_reminder_button() -> dict:
    booking_id = _create_unpaid_booking("ManualE", issue_days_ago=0)
    with _automation_patches():
        ok, msg = services.send_payment_reminder_now(booking_id)
    row = dict(db.get_booking(booking_id))
    failed = []
    if not ok:
        failed.append("Manual send failed: {0}".format(msg))
    if not (row.get("payment_reminder_1_sent_at") or "").strip():
        failed.append("Manual send did not set payment_reminder_1_sent_at.")
    if not _logs(booking_id, automation.AUTOMATION_PAYMENT_REMINDER_1_SENT):
        failed.append("No automation log for manual reminder.")
    return {
        "name": "Test E — Manual reminder button works",
        "pass": not failed,
        "details": failed or ["Send payment reminder now sent reminder 1."],
        "booking_id": booking_id,
        "message": msg,
    }


def main() -> int:
    db.init_db()
    _enable_automation()
    results = [
        test_unpaid_sends_reminder(),
        test_paid_skips_reminder(),
        test_cancelled_skips_reminder(),
        test_duplicate_blocked(),
        test_manual_reminder_button(),
    ]
    payload = {
        "phase": 16,
        "feature": "unpaid_invoice_reminder_automation",
        "results": results,
        "all_pass": all(r["pass"] for r in results),
    }
    out_path = RESULTS_DIR / "phase16_results.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nPhase 16 — Unpaid Invoice Reminder Automation E2E\n")
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
