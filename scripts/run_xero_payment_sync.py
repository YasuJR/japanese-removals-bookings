#!/usr/bin/env python3
"""Sync paid Xero invoices to booking payment_status (cron entrypoint)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
import services


def main() -> int:
    db.init_db()
    result = services.sync_xero_payments()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
