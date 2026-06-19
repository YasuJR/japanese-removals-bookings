#!/usr/bin/env python3
"""Render CEO dashboard HTML preview for offline viewing."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "test_results" / "ceo_dashboard"
OUT.mkdir(parents=True, exist_ok=True)

import database as db
from app import app


def main() -> None:
    db.init_db()
    user = db.get_staff_by_username("admin")
    if not user and db.staff_user_count() > 0:
        user = db.get_staff_user(1)
    if not user:
        raise SystemExit("No staff user — run create_staff.py first.")

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
            sess["username"] = user["username"]
        response = client.get("/")
        html_path = OUT / "preview.html"
        html_path.write_bytes(response.data)
        print("Preview saved to {0}".format(html_path))


if __name__ == "__main__":
    main()
