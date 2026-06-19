#!/usr/bin/env python3
"""Re-sync all linked Xero draft invoices with clean line descriptions."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
import services
from integrations import xero, xero_branding


def main() -> int:
    db.init_db()
    if not xero.is_ready():
        print("Xero not ready — connect and set tenant ID first.")
        return 1

    ok, msg = xero_branding.sync_branding_theme(xero._api_request)
    print("Branding sync:", msg if ok else "FAILED: {0}".format(msg))

    rows = db.list_all()
    updated = 0
    failed = 0
    for row in rows:
        booking = services.booking_to_dict(row)
        invoice_id = (booking.get("xero_invoice_id") or "").strip()
        if not xero.is_real_invoice_id(invoice_id):
            continue
        if not xero.is_draft_invoice(booking):
            print("Skip booking #{0} — invoice not draft.".format(booking["id"]))
            continue
        ok, msg, _inv = xero.sync_invoice_record(booking, confirm_new=False)
        if ok:
            updated += 1
            print("Booking #{0}: {1}".format(booking["id"], msg))
        else:
            failed += 1
            print("Booking #{0} FAILED: {1}".format(booking["id"], msg))

    print("Done. Updated {0}, failed {1}.".format(updated, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
