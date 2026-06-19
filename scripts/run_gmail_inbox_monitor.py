#!/usr/bin/env python3
"""Poll Gmail inbox and create Pending bookings from new emails."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
from integrations import gmail_inbox


def main() -> int:
    db.init_db()
    messages = gmail_inbox.poll_inbox()
    for line in messages:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
