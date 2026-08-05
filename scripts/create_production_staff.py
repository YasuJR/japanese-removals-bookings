#!/usr/bin/env python3
"""Create or reset a staff user in the production PostgreSQL database.

Requires DATABASE_URL (Render PostgreSQL connection string).

Usage:
  DATABASE_URL=postgresql://... python scripts/create_production_staff.py admin 'YourPassword'
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: DATABASE_URL=postgresql://... python scripts/create_production_staff.py <username> <password>")
        return 1

    username, password = sys.argv[1], sys.argv[2]
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return 1

    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        print("Set DATABASE_URL to your Render PostgreSQL connection string.")
        return 1
    if not database_url.startswith("postgres"):
        print("DATABASE_URL must be a PostgreSQL URL.")
        return 1

    os.environ["DATABASE_URL"] = database_url
    os.environ["PRODUCTION"] = "true"

    import auth
    import database as db
    from db_backend import is_postgres

    if not is_postgres():
        print("DATABASE_URL not loaded — check the connection string.")
        return 1

    db.init_db()
    existing = db.get_staff_by_username(username)
    password_hash = auth.hash_password(password)

    if existing:
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE staff_users SET password_hash = %s, display_name = %s WHERE id = %s",
                (password_hash, username, int(existing["id"])),
            )
            conn.commit()
        print("Staff password updated for:", username)
        return 0

    user_id = db.create_staff_user(username, password_hash, display_name=username)
    print("Staff user created (id={0}): {1}".format(user_id, username))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
