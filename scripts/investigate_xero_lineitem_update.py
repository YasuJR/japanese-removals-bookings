#!/usr/bin/env python3
"""
Investigate Xero invoice line-item updates for INV-0005 (or booking id).

Usage:
  python3 scripts/investigate_xero_lineitem_update.py INV-0005
  python3 scripts/investigate_xero_lineitem_update.py INV-0005 --reuse-ids
  python3 scripts/investigate_xero_lineitem_update.py INV-0005 --recreate
"""

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
import services
from integrations import xero


def _summarise_line(line: dict, index: int) -> dict:
    return {
        "index": index,
        "LineItemID": line.get("LineItemID"),
        "Description": line.get("Description"),
        "Quantity": line.get("Quantity"),
        "UnitAmount": line.get("UnitAmount"),
        "LineAmount": line.get("LineAmount"),
    }


def _print_lines(title: str, lines: list) -> None:
    print("\n=== {0} ===".format(title))
    for index, line in enumerate(lines or []):
        print(json.dumps(_summarise_line(line, index), indent=2))


def find_booking(ref: str):
    ref = ref.strip()
    if ref.isdigit():
        row = db.get_booking(int(ref))
        return services.booking_to_dict(row) if row else None
    for row in db.list_all():
        booking = dict(row)
        if (booking.get("invoice_number") or "").upper() == ref.upper():
            return services.booking_to_dict(row)
    return None


def _build_update_payload(booking, before, reuse_ids: bool):
    payload, totals, booking_id, issue_date, due_date = xero._draft_invoice_payload(
        booking, existing_invoice=before
    )
    payload["InvoiceID"] = before["InvoiceID"]
    payload["Status"] = "DRAFT"
    line_items = copy.deepcopy(payload["LineItems"])
    if reuse_ids:
        existing_lines = list(before.get("LineItems") or [])
        for index, item in enumerate(line_items):
            if index < len(existing_lines) and existing_lines[index].get("LineItemID"):
                item["LineItemID"] = existing_lines[index]["LineItemID"]
    payload["LineItems"] = line_items
    return payload, totals


def main() -> int:
    db.init_db()
    if not xero.is_ready():
        print("Xero not ready.")
        return 1

    args = [a for a in sys.argv[1:] if a.startswith("--")]
    refs = [a for a in sys.argv[1:] if not a.startswith("--")]
    ref = refs[0] if refs else "INV-0005"
    reuse_ids = "--reuse-ids" in args
    recreate = "--recreate" in args or not reuse_ids

    booking = find_booking(ref)
    if not booking:
        print("Booking not found:", ref)
        return 1

    invoice_id = (booking.get("xero_invoice_id") or "").strip()
    if not xero.is_real_invoice_id(invoice_id):
        print("No linked Xero invoice.")
        return 1

    print("Booking #{0}  Invoice {1}  Xero ID {2}".format(
        booking["id"], booking.get("invoice_number"), invoice_id
    ))

    before = xero.fetch_invoice(invoice_id)
    if not before:
        print("Could not fetch invoice from Xero.")
        return 1

    print("\n=== STEP 1 — Fetch invoice from Xero ===")
    _print_lines("STEP 2 — Existing LineItem IDs and fields (BEFORE)", before.get("LineItems"))

    mode = "WITH LineItemID reuse" if reuse_ids else "WITHOUT LineItemID (delete + recreate)"
    payload, totals = _build_update_payload(booking, before, reuse_ids=reuse_ids)

    print("\n=== STEP 3 — UPDATE REQUEST ({0}) ===".format(mode))
    print(json.dumps(payload.get("LineItems"), indent=2))

    try:
        result = xero._api_request("POST", "Invoices", {"Invoices": [payload]})
    except RuntimeError as exc:
        print("\nXero rejected the update:")
        print(str(exc))
        return 1

    returned = (result.get("Invoices") or [{}])[0]
    after = xero.fetch_invoice(invoice_id) or returned

    _print_lines("STEP 4 — LineItems AFTER update (stored in Xero)", after.get("LineItems"))

    old_lines = before.get("LineItems") or []
    new_lines = after.get("LineItems") or []
    print("\n=== STEP 5 — Field overwrite check (line 0) ===")
    if old_lines and new_lines:
        old0 = old_lines[0]
        new0 = new_lines[0]
        print("LineItemID changed:", old0.get("LineItemID") != new0.get("LineItemID"))
        print("Description changed:", old0.get("Description") != new0.get("Description"))
        print("Quantity changed:", old0.get("Quantity") != new0.get("Quantity"))
        print("UnitAmount changed:", old0.get("UnitAmount") != new0.get("UnitAmount"))
        print("LineAmount changed:", old0.get("LineAmount") != new0.get("LineAmount"))
        print("Description still has company block:", "Japanese Removals" in (new0.get("Description") or ""))
        print("\nNew line 0 Description:")
        print(new0.get("Description"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
