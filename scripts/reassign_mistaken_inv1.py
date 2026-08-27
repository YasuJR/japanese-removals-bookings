#!/usr/bin/env python3
"""Inspect and reassign the stray stored INV1 from the sequence-reset bug.

Dry-run by default. Pass --apply to write the one invoice_number change and
raise invoice_sequence.next_number to max+2.

  python scripts/reassign_mistaken_inv1.py
  python scripts/reassign_mistaken_inv1.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
import invoice_numbering


def _print_report(report: dict) -> None:
    print(json.dumps(report, default=str, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the INV1 → next unused number change (default is dry-run)",
    )
    args = parser.parse_args()

    db.init_db()
    dry = db.reassign_mistaken_invoice_one(dry_run=True)
    target = dry.get("target_id")
    old = dry.get("old_number")
    new = dry.get("new_number")
    print("Target stored INV1:", "booking id={0} {1} ({2})".format(
        target,
        invoice_numbering.format_invoice_number(old) if old else "(none)",
        dry.get("customer_name") or "",
    ) if target else "(none)")
    print("Current max invoice number:", dry.get("max_used_before"))
    print("Planned number:", "INV{0}".format(new) if new else "(none)")
    print("Next new invoice after apply would be:", "INV{0}".format(new + 1) if new else "(none)")
    _print_report(dry)

    if not args.apply:
        print("Dry-run only. Re-run with --apply to write this one invoice_number.")
        return 0

    if not target or not new:
        print("Nothing to apply.")
        return 0

    applied = db.reassign_mistaken_invoice_one(dry_run=False)
    print("Applied:")
    _print_report(applied)
    return 0 if applied.get("changed") or applied.get("skipped") == "no_stray_stored_inv1" else 1


if __name__ == "__main__":
    raise SystemExit(main())
