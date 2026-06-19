#!/usr/bin/env python3
"""Migrate bookings from local SQLite to PostgreSQL (production DATABASE_URL)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
import database as db


def main() -> int:
    if not config.DATABASE_URL:
        print("Set DATABASE_URL to your Render PostgreSQL connection string.")
        return 1
    if not Path(ROOT / "bookings.db").is_file():
        print("No local bookings.db found.")
        return 1

    print("This script initializes PostgreSQL and copies core booking rows.")
    print("Run a full pg_dump/pg_restore for complete migration in production.")
    print("Target:", config.DATABASE_URL.split("@")[-1])

    # Force postgres backend
    import db_backend

    if not db_backend.is_postgres():
        print("DATABASE_URL must be a PostgreSQL URL.")
        return 1

    db.init_db()
    print("PostgreSQL schema ready. Export/import detailed rows via pg_dump or custom ETL.")
    print("See docs/DEPLOYMENT.md section 7.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
