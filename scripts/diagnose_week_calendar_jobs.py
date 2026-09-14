#!/usr/bin/env python3
"""Compare DB bookings in a week vs Staff Portal calendar week payload."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import database as db
import job_status
from crew import crew_from_storage
from display_dates import normalize_move_date
from staff_portal import (
    CAL_VIEW_WEEK,
    _calendar_week_job_ids,
    _should_hide_status,
    build_staff_portal,
    staff_calendar_week_bounds,
)


def _db_week_bookings(start_iso: str, end_iso: str, staff_name: str = "") -> list:
    rows = [dict(row) for row in db.list_between_dates(start_iso, end_iso)]
    matched = []
    for row in rows:
        if _should_hide_status(row):
            continue
        move_iso = normalize_move_date(row.get("move_date"))
        if not move_iso or move_iso < start_iso or move_iso > end_iso:
            continue
        if staff_name and staff_name not in crew_from_storage(row.get("crew")):
            continue
        matched.append(row)
    return matched


def diagnose(today: date, week_offset: int = 0, staff_view: str = "all") -> int:
    db.init_db()
    start_iso, end_iso = staff_calendar_week_bounds(today, week_offset)
    if staff_view == "all":
        db_rows = _db_week_bookings(start_iso, end_iso, "")
        portal = build_staff_portal(
            view_staff_id="all",
            range_key="calendar",
            today=today,
            week_offset=week_offset,
            cal_view=CAL_VIEW_WEEK,
        )
    else:
        db_rows = _db_week_bookings(start_iso, end_iso, staff_view)
        portal = build_staff_portal(
            staff_view,
            "calendar",
            today,
            week_offset=week_offset,
            cal_view=CAL_VIEW_WEEK,
        )
    shown_ids = set(_calendar_week_job_ids(portal.get("calendar") or {}))
    db_ids = {int(row["id"]) for row in db_rows}
    missing = sorted(db_ids - shown_ids)
    extra = sorted(shown_ids - db_ids)
    print("Week {0} .. {1}  view={2}".format(start_iso, end_iso, view))
    print("DB bookings (visible): {0}".format(len(db_ids)))
    print("Week calendar cards:   {0}".format(len(shown_ids)))
    if missing:
        print("\nMissing from week calendar:")
        for bid in missing:
            row = next(r for r in db_rows if int(r["id"]) == bid)
            print(
                "  id={0} date={1} start={2} status={3} crew={4} customer={5}".format(
                    bid,
                    normalize_move_date(row.get("move_date")),
                    row.get("start_time"),
                    job_status.display(row),
                    row.get("crew"),
                    row.get("customer_name"),
                )
            )
    if extra:
        print("\nExtra on week calendar (unexpected):", extra)
    if not missing and not extra:
        print("OK — week calendar matches DB for this range.")
    return 1 if missing else 0


if __name__ == "__main__":
    today = date.today()
    offset = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    staff = sys.argv[2] if len(sys.argv) > 2 else "all"
    raise SystemExit(diagnose(today, offset, staff))
