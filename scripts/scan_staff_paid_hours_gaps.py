#!/usr/bin/env python3
"""List bookings where Staff Portal Paid Hours were blank before invoice fallback."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
import job_status
import staff_job_times
from outstanding_invoices_data import invoice_has_been_issued


def _pattern(booking: dict) -> str:
    status = job_status.display(booking)
    has_actual_cols = bool(
        str(booking.get("actual_start_time") or "").strip()
        or str(booking.get("actual_finish_time") or "").strip()
        or booking.get("actual_duration") not in (None, "", 0)
    )
    has_times = bool(
        str(booking.get("start_time") or "").strip()
        and str(booking.get("finish_time") or "").strip()
    )
    if has_actual_cols:
        return "has_actual_columns"
    if status in ("Completed", "Invoiced", "Paid"):
        return "completed_status"
    if invoice_has_been_issued(booking) and (has_times or booking.get("duration_hours")):
        return "invoice_confirmed"
    if status == "Confirmed" and has_times:
        return "confirmed_schedule_only"
    return "other"


def scan():
    db.init_db()
    rows = [dict(row) for row in db.list_all()]
    gaps_before = []
    fixed_by_invoice = []
    for row in rows:
        if job_status.display(row) == "Cancelled":
            continue
        paid = staff_job_times.paid_hours(row)
        pattern = _pattern(row)
        if paid is None and pattern == "invoice_confirmed":
            gaps_before.append(row)
        elif paid is not None and pattern == "invoice_confirmed":
            fixed_by_invoice.append(row)

    print("Invoice-confirmed jobs now with Paid Hours:", len(fixed_by_invoice))
    print("Invoice-confirmed jobs still missing Paid Hours:", len(gaps_before))
    for row in gaps_before[:50]:
        print(
            "  id={id} {customer} {move_date} status={status} invoice={invoice_number}".format(
                id=row.get("id"),
                customer=row.get("customer_name"),
                move_date=row.get("move_date"),
                status=row.get("status"),
                invoice_number=row.get("invoice_number") or row.get("invoice_status"),
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(scan())
