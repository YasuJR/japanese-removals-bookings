#!/usr/bin/env python3
"""Live smoke test: booking defaults, extra charges, Xero draft sync."""

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
import invoice
import services
from integrations import company_config, xero


def main() -> int:
    db.init_db()
    defaults = company_config.booking_form_defaults()
    move_date = (date.today() + timedelta(days=14)).isoformat()

    print("=== Company defaults ===")
    print("Phone:", defaults["phone"])
    print("Email:", defaults["email"])
    print("Hourly rate:", defaults["hourly_rate"])
    print("Callout fee:", defaults["callout_fee"])
    print("GST enabled:", defaults["gst_enabled"])
    print("Crew:", defaults["crew"])
    print()

    booking_id = db.create_booking(
        customer_name="Smoke Test Customer",
        phone=defaults["phone"],
        email="smoke-test@example.com",
        pickup_address="1 Test St, Perth WA",
        delivery_address="2 Demo Ave, Perth WA",
        move_date=move_date,
        num_movers=3,
        notes="Live smoke test — safe to delete",
        start_time="08:00",
        finish_time="13:00",
        duration_hours="5",
        crew="Yasu,Tom,Ken",
        hourly_rate=defaults["hourly_rate"],
        callout_fee=defaults["callout_fee"],
        gst_enabled=defaults["gst_enabled"],
        payment_status="Unpaid",
        invoice_status="",
        status="Scheduled",
    )

    data = {
        "extra_charges": [
            {"description": "Stairs Fee", "quantity": 1, "unit_price": 50},
        ],
        "invoice_custom_text": "Smoke test invoice text",
        "invoice_bank_account_name": "",
        "invoice_bank_bsb": "",
        "invoice_bank_account": "",
    }
    services._persist_booking_extras(booking_id, data)

    booking = services.booking_to_dict(db.get_booking(booking_id))
    totals = invoice.calculate_invoice_totals(booking)
    summary = invoice.invoice_summary(booking)

    print("=== Booking #{0} created ===".format(booking_id))
    print("Move date:", move_date)
    print("Extra charge: Stairs Fee $50")
    print()
    print("=== Invoice summary (GST inclusive) ===")
    print("Labour: ${0}/hr × {1}h + callout ${2}".format(
        totals["hourly_rate"], totals["hours"], totals["callout_fee"]
    ))
    print("Net sales (ex GST):", invoice.format_aud(summary["net_sales"]))
    print("GST:", invoice.format_aud(summary["gst_amount"]))
    print("Total (incl. GST):", invoice.format_aud(summary["total"]))
    print()

    if not xero.is_ready():
        print("Xero not connected — booking created but draft not synced.")
        print("Open: http://localhost:5001/bookings/{0}/edit".format(booking_id))
        return 0

    ok, msg, inv = xero.sync_invoice_record(booking, confirm_new=False)
    print("=== Xero draft ===")
    print("Success:", ok)
    print("Message:", msg)
    if inv:
        invoice_id = inv.get("InvoiceID") or ""
        print("Invoice ID:", invoice_id)
        print("Invoice number:", inv.get("InvoiceNumber") or "(draft)")
        print("Xero total:", inv.get("Total"))
        print("LineAmountTypes:", inv.get("LineAmountTypes"))
        print("Open:", xero.invoice_url(invoice_id) if invoice_id else "n/a")

        booking2 = services.booking_to_dict(db.get_booking(booking_id))
        booking2["hourly_rate"] = 200
        booking2["notes"] = "Updated notes from smoke test"
        db.update_booking(
            booking_id=booking_id,
            customer_name=booking2["customer_name"],
            phone=booking2["phone"],
            email=booking2["email"],
            pickup_address=booking2["pickup_address"],
            delivery_address=booking2["delivery_address"],
            move_date=booking2["move_date"],
            num_movers=booking2["num_movers"],
            notes=booking2["notes"],
            start_time=booking2["start_time"],
            finish_time=booking2["finish_time"],
            duration_hours=booking2["duration_hours"],
            crew=",".join(booking2.get("crew") or []),
            hourly_rate=200,
            callout_fee=booking2["callout_fee"],
            gst_enabled=booking2["gst_enabled"],
            payment_status=booking2["payment_status"],
            invoice_status=booking2["invoice_status"],
            status=booking2["status"],
        )
        booking2 = services.booking_to_dict(db.get_booking(booking_id))
        ok2, msg2, inv2 = xero.sync_invoice_record(booking2, confirm_new=False)
        print()
        print("=== Xero draft update (hourly rate $200) ===")
        print("Success:", ok2)
        print("Message:", msg2)
        if inv2:
            print("Updated Xero total:", inv2.get("Total"))

    print()
    print("Edit booking: http://localhost:5001/bookings/{0}/edit".format(booking_id))
    print("Job sheet PDF: http://localhost:5001/bookings/{0}/job-sheet.pdf".format(booking_id))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
