#!/usr/bin/env python3
"""Verify GST-inclusive invoice math and Xero payload layout."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import invoice
from integrations import xero, xero_branding

SAMPLE = {
    "id": 1,
    "customer_name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "0400000000",
    "move_date": "2026-06-10",
    "hourly_rate": 180,
    "callout_fee": 90,
    "duration_hours": "5",
    "gst_enabled": 1,
    "crew": "Yasu,Tom,Ken",
}


def main():
    after = invoice.calculate_invoice_totals(SAMPLE)
    payload, totals, *_ = xero._draft_invoice_payload(SAMPLE)
    desc = payload["LineItems"][0]["Description"]
    header = xero_branding.invoice_header_lines()

    print("=== Invoice total ===")
    print("Total (incl. GST):", invoice.format_aud(after["total"]))
    print()
    print("=== Xero header (org/branding — above line items) ===")
    print("\n".join(header))
    print()
    print("=== Labour line description (service only) ===")
    print(desc)
    print()

    assert after["total"] == 990.0
    assert payload["LineAmountTypes"] == "Inclusive"
    assert "Japanese Removals" not in desc
    assert "Bank Details" not in desc
    assert "0481 089 573" not in desc
    assert "Moving Labour" in desc
    assert "5.0 hrs" in desc
    assert "Crew:" in desc
    assert "Callout fee" in payload["LineItems"][1]["Description"]
    assert header[0] == "Japanese Removals"
    assert header[1].startswith("Phone:")
    assert "Bank Details" in header
    print("All checks passed.")


if __name__ == "__main__":
    main()
