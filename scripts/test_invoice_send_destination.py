#!/usr/bin/env python3
"""Send Invoice destination priority tests."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from integrations import invoice_send


def test_email_only():
    dest = invoice_send.resolve_send_destination(
        {"email": "customer@example.com", "phone": ""}
    )
    assert dest["can_send"] and dest["method"] == "email"
    assert dest["destination"] == "customer@example.com"
    assert dest["destination_display"] == "customer@example.com"


def test_phone_only():
    dest = invoice_send.resolve_send_destination(
        {"email": "", "phone": "0412987654"}
    )
    assert dest["can_send"] and dest["method"] == "sms"
    assert dest["destination"] == "0412987654"
    assert dest["destination_display"] == "0412 987 654"


def test_email_wins_when_both_present():
    dest = invoice_send.resolve_send_destination(
        {"email": "customer@example.com", "phone": "0412987654"}
    )
    assert dest["can_send"] and dest["method"] == "email"
    assert dest["destination"] == "customer@example.com"


def test_neither_contact_disables_send():
    dest = invoice_send.resolve_send_destination({"email": "", "phone": ""})
    assert not dest["can_send"]
    assert "required" in dest["blocked_reason"].lower()


def test_company_defaults_do_not_send_sms_to_office():
    """Booking #7 pattern: company email + company phone must not SMS the office."""
    dest = invoice_send.resolve_send_destination(
        {
            "email": "info@japaneseremovals.com.au",
            "phone": "0481 089 573",
        }
    )
    assert not dest["can_send"], dest
    assert dest["method"] == ""


def test_valid_email_with_company_phone_uses_email():
    dest = invoice_send.resolve_send_destination(
        {
            "email": "booking7@example.com",
            "phone": "0481 089 573",
        }
    )
    assert dest["can_send"] and dest["method"] == "email"
    assert dest["destination"] == "booking7@example.com"


def test_placeholder_email_with_customer_phone_uses_sms():
    dest = invoice_send.resolve_send_destination(
        {
            "email": "info@japaneseremovals.com.au",
            "phone": "0412345678",
        }
    )
    assert dest["can_send"] and dest["method"] == "sms"
    assert dest["destination_display"] == "0412 345 678"


def test_invalid_email_with_customer_phone_uses_sms():
    dest = invoice_send.resolve_send_destination(
        {"email": "not-an-email", "phone": "0412345678"}
    )
    assert dest["can_send"] and dest["method"] == "sms"


def main():
    tests = [
        test_email_only,
        test_phone_only,
        test_email_wins_when_both_present,
        test_neither_contact_disables_send,
        test_company_defaults_do_not_send_sms_to_office,
        test_valid_email_with_company_phone_uses_email,
        test_placeholder_email_with_customer_phone_uses_sms,
        test_invalid_email_with_customer_phone_uses_sms,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS:", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", fn.__name__, exc)
    print("\n{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
