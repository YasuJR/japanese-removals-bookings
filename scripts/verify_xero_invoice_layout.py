#!/usr/bin/env python3
"""Sync branding, create layout test booking + Xero draft, verify invoice layout."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
import invoice
import services
from integrations import company_config, xero, xero_branding


def _assert(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    line = "[{0}] {1}".format(status, label)
    if detail:
        line = "{0} — {1}".format(line, detail)
    print(line)
    return ok


def main() -> int:
    db.init_db()
    defaults = company_config.booking_form_defaults()

    print("=== Step 1: Sync invoice branding to Xero ===")
    if not xero.is_ready():
        print("Xero not connected.")
        return 1
    ok, msg = xero.sync_invoice_branding()
    print(msg)
    branding_synced = ok

    org_text = ""
    if branding_synced:
        org = xero._api_request("GET", "Organisation")
        orgs = org.get("Organisations") or [{}]
        org_row = orgs[0]
        addr = (org_row.get("Addresses") or [{}])[0]
        org_text = "\n".join(
            part
            for part in [
                org_row.get("Name") or "",
                addr.get("AddressLine1") or "",
                addr.get("AddressLine2") or "",
                addr.get("AddressLine3") or "",
                addr.get("AddressLine4") or "",
                addr.get("City") or "",
            ]
            if part
        )
        print("\n=== Xero organisation header (above line items) ===")
        print(org_text)
        print()
    else:
        print("\n(Warning: branding sync failed — invoice line items will still be created.)")
        print("Reconnect Xero with Settings scope, then re-run sync.\n")

    print("=== Step 2: Create layout test booking ===")
    move_date = (date.today() + timedelta(days=21)).isoformat()
    booking_id = db.create_booking(
        customer_name="Layout Test Customer",
        phone="0400000001",
        email="layout-test@example.com",
        pickup_address="99 Pickup Rd, Perth WA 6000",
        delivery_address="88 Delivery St, Fremantle WA 6160",
        move_date=move_date,
        num_movers=3,
        notes="Xero layout verification — safe to delete",
        start_time="08:00",
        finish_time="13:00",
        duration_hours="5",
        crew="Yasu,Tom,Ken",
        hourly_rate=defaults["hourly_rate"],
        callout_fee=defaults["callout_fee"],
        gst_enabled=defaults["gst_enabled"],
        payment_status="Unpaid",
        invoice_status="",
        status="Completed",
    )
    services._persist_booking_extras(
        booking_id,
        {
            "extra_charges": [],
            "invoice_custom_text": "",
            "invoice_bank_account_name": "",
            "invoice_bank_bsb": "",
            "invoice_bank_account": "",
        },
    )
    booking = services.booking_to_dict(db.get_booking(booking_id))
    totals = invoice.calculate_invoice_totals(booking)
    print("Booking #{0} — total (incl. GST): {1}".format(
        booking_id, invoice.format_aud(totals["total"])
    ))

    print("\n=== Step 3: Create Xero draft invoice ===")
    ok, msg, inv = xero.sync_invoice_record(booking, confirm_new=False)
    print(msg)
    if not ok or not inv:
        return 1

    invoice_id = inv.get("InvoiceID") or ""
    invoice_number = inv.get("InvoiceNumber") or ""
    print("Invoice:", invoice_number or "(draft)", invoice_id)
    print("URL:", xero.invoice_url(invoice_id))

    live = xero.fetch_invoice(invoice_id) or inv
    online_url = ""
    try:
        online = xero._api_request("GET", "Invoices/{0}/OnlineInvoice".format(invoice_id))
        online_url = ((online.get("OnlineInvoices") or [{}])[0].get("OnlineInvoiceUrl") or "")
    except Exception:
        online_url = ""
    line_text = "\n".join(
        (item.get("Description") or "") for item in (live.get("LineItems") or [])
    )
    all_invoice_text = json.dumps(live, indent=2)

    print("\n=== Verification ===")
    checks = []
    if branding_synced and org_text:
        checks.extend([
            _assert(
                "Japanese Removals above line items",
                "Japanese Removals" in org_text,
                org_text.split("\n")[0] if org_text else "",
            ),
            _assert(
                "Phone 0481 089 573",
                "0481 089 573" in org_text,
            ),
            _assert(
                "Email info@japaneseremovals.com.au",
                "info@japaneseremovals.com.au" in org_text,
            ),
            _assert(
                "Bank details (JR West, BSB, account)",
                "JR West" in org_text
                and "036308" in org_text
                and "405623" in org_text,
            ),
        ])
    else:
        print("[SKIP] Organisation header checks — branding sync requires Settings scope.")

    checks.extend([
        _assert(
            "Pickup address NOT on invoice lines",
            "Pickup Rd" not in line_text and "99 Pickup" not in line_text,
        ),
        _assert(
            "Delivery address NOT on invoice lines",
            "Delivery St" not in line_text and "Fremantle" not in line_text,
        ),
        _assert(
            "Company block NOT in line descriptions",
            "Japanese Removals" not in line_text
            and "Bank Details" not in line_text
            and "0481 089 573" not in line_text,
        ),
        _assert(
            "LineAmountTypes Inclusive",
            (live.get("LineAmountTypes") or inv.get("LineAmountTypes")) == "Inclusive",
        ),
        _assert(
            "Total $990.00 GST inclusive",
            float(live.get("Total") or 0) == 990.0,
            "Total={0}".format(live.get("Total")),
        ),
    ])

    print("\n=== Line item descriptions ===")
    for item in live.get("LineItems") or []:
        print("---")
        print(item.get("Description") or "")

    out_path = Path(ROOT) / "credentials" / "xero_layout_verification.json"
    out_path.write_text(
        json.dumps(
            {
                "booking_id": booking_id,
                "invoice_id": invoice_id,
                "invoice_number": invoice_number,
                "invoice_url": xero.invoice_url(invoice_id),
                "online_invoice_url": online_url,
                "organisation_header": org_text,
                "line_descriptions": line_text,
                "total": live.get("Total"),
                "line_amount_types": live.get("LineAmountTypes"),
                "checks_passed": all(checks),
            },
            indent=2,
        )
    )
    print("\nSaved:", out_path)
    print("Edit booking: http://localhost:5001/bookings/{0}/edit".format(booking_id))

    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
