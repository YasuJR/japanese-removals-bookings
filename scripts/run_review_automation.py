#!/usr/bin/env python3
"""Send due Google review requests."""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
from integrations import review_automation


def main() -> int:
    db.init_db()
    messages = review_automation.process_due_requests(datetime.utcnow())
    if messages:
        for line in messages:
            print(line)
    else:
        print("(no due review requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
