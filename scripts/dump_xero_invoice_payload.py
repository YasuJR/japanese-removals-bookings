#!/usr/bin/env python3
"""
Print the exact Xero invoice JSON payload (LineItems Description) for a booking.

Usage:
  python3 scripts/dump_xero_invoice_payload.py           # sample booking
  python3 scripts/dump_xero_invoice_payload.py 1        # booking id
  python3 scripts/dump_xero_invoice_payload.py INV-0005 # invoice number
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
import services
from integrations import xero

SAMPLE = {
    "id": 0,
    "customer_name": "Sample Customer",
    "email": "sample@example.com",
    "phone": "0400000000",
    "move_date": "2026-07-01",
    "hourly_rate": 180,
    "callout_fee": 90,
    "duration_hours": "5",
    "gst_enabled": 1,
    "crew": "Yasu,Tom,Ken",
    "extra_charges": [],
}


def find_booking(arg: str):
    if arg.isdigit():
        row = db.get_booking(int(arg))
        return services.booking_to_dict(row) if row else None
    for row in db.list_all():
        booking = dict(row)
        if (booking.get("invoice_number") or "").strip().upper() == arg.upper():
            return services.booking_to_dict(row)
    return None


def main() -> None:
    db.init_db()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    booking = find_booking(arg) if arg else SAMPLE
    if not booking:
        print("Booking not found for:", arg)
        raise SystemExit(1)

    invoice_id = (booking.get("xero_invoice_id") or "").strip()
    existing = xero.fetch_invoice(invoice_id) if xero.is_ready() and invoice_id else None
    payload, totals, booking_id, issue_date, due_date = xero._draft_invoice_payload(
        booking, existing_invoice=existing
    )

    print("Booking ID:", booking.get("id", "(sample)"))
    print("Invoice number:", booking.get("invoice_number") or "—")
    print("Issue date:", issue_date, "Due:", due_date)
    print("Total (incl. GST):", totals["total"])
    print()
    print("=== Full POST payload (Invoices[0]) ===")
    print(json.dumps(payload, indent=2))
    print()
    print("=== LineItems Description values (sent to Xero) ===")
    for index, item in enumerate(payload.get("LineItems") or []):
        print("--- Line {0} ---".format(index))
        print(item.get("Description", ""))
        print()

    if existing:
        print("=== Currently stored in Xero (for comparison) ===")
        for index, item in enumerate(existing.get("LineItems") or []):
            print("--- Stored line {0} ---".format(index))
            print(item.get("Description", ""))
            print()


if __name__ == "__main__":
    main()
