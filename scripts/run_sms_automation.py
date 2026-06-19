#!/usr/bin/env python3
"""Run scheduled SMS automations (move reminders + overdue payment reminders)."""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
from integrations import sms_automation


def main() -> int:
    db.init_db()
    results = sms_automation.run_scheduled_automations(date.today())
    for label, messages in results.items():
        print("[{0}]".format(label))
        if messages:
            for line in messages:
                print("  ", line)
        else:
            print("  (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
