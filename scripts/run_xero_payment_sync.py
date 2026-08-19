#!/usr/bin/env python3
"""Sync paid Xero invoices to booking payment_status (Render cron entrypoint)."""

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import production_bootstrap

production_bootstrap.bootstrap_production()

import database as db
import services

logging.basicConfig(level=logging.INFO, format="%(message)s")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in args
    db.init_db()
    result = services.sync_xero_payments(source="cron", dry_run=dry_run)
    for line in result.get("log_lines") or []:
        print(line)
    print(json.dumps({k: result[k] for k in result if k != "log_lines"}, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
