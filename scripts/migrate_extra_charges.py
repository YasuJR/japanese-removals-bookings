#!/usr/bin/env python3
"""Apply booking extra-charges migration (safe to run multiple times)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db


def main() -> None:
    db.init_db()
    with db.get_connection() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(bookings)").fetchall()
        }
    print("booking_extra_charges table:", "booking_extra_charges" in tables)
    for name in (
        "invoice_custom_text",
        "invoice_bank_account_name",
        "invoice_bank_bsb",
        "invoice_bank_account",
    ):
        print("{0} column:".format(name), name in columns)
    print("Migration complete.")


if __name__ == "__main__":
    main()
