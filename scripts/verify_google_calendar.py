#!/usr/bin/env python3
"""Verify Google Calendar OAuth, API access, and optional booking sync."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations import google_calendar


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--booking-id",
        type=int,
        help="After connection checks, sync this booking to Google Calendar.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full diagnostic details as JSON.",
    )
    args = parser.parse_args()

    ok, msg, details = google_calendar.verify_connection()
    print("=== Google Calendar verification ===")
    print(msg)
    if args.json:
        print(json.dumps(details, indent=2))
    else:
        for key in (
            "credentials_present",
            "token_present",
            "calendar_id",
            "calendar_summary",
            "calendar_timezone",
            "upcoming_event_count",
        ):
            if key in details:
                print("{0}: {1}".format(key.replace("_", " ").title(), details[key]))
        for item in details.get("upcoming_events") or []:
            print("  · {0} — {1}".format(item.get("start", ""), item.get("summary", "")))

    if not ok:
        return 1

    if args.booking_id:
        print()
        print("=== Booking sync test (#{0}) ===".format(args.booking_id))
        sync_ok, sync_msg, sync_details = google_calendar.verify_booking_sync(
            args.booking_id
        )
        print(sync_msg)
        if sync_details.get("event_id"):
            print("Event ID: {0}".format(sync_details["event_id"]))
        if not sync_ok:
            return 1

    print()
    print("All calendar checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
