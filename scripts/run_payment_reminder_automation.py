#!/usr/bin/env python3
"""Run Phase 16 unpaid invoice reminder automation (cron entrypoint)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
from integrations import payment_reminder_automation


def main() -> int:
    db.init_db()
    messages = payment_reminder_automation.process_due_reminders()
    if messages:
        print("Processed {0} reminder(s):".format(len(messages)))
        for line in messages:
            print("  - {0}".format(line))
    else:
        print("No due payment reminders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
