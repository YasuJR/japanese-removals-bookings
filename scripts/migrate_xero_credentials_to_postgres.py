#!/usr/bin/env python3
"""
One-shot: copy live Xero JSON files into PostgreSQL without deleting the files.

Run this in the Render Web Shell on the currently connected instance BEFORE
deploying the Postgres-auth build, so the next deploy does not lose Xero.

  python scripts/migrate_xero_credentials_to_postgres.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
import database as db

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "xero_settings.json"
TOKEN_PATH = Path(config.XERO_TOKEN_FILE)
STORAGE_KEY = "xero"


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit("Could not read {0}: {1}".format(path, exc))


def main() -> int:
    db.init_db()
    settings = _load_json(SETTINGS_PATH)
    token = _load_json(TOKEN_PATH)
    existing = db.get_integration_settings(STORAGE_KEY) or {}

    payload = dict(existing)
    for key in (
        "client_id",
        "client_secret",
        "tenant_id",
        "auto_create_draft_on_confirmed",
        "auto_create_on_booking_create",
    ):
        if key in settings and settings.get(key) not in (None, ""):
            payload[key] = settings[key]
        elif key not in payload:
            continue

    if isinstance(token, dict) and token.get("access_token") and token.get("refresh_token"):
        payload["token"] = token
    elif existing.get("token"):
        payload["token"] = existing["token"]

    missing = [
        name
        for name, ok in (
            ("client_id", bool((payload.get("client_id") or "").strip())),
            ("client_secret", bool((payload.get("client_secret") or "").strip())),
            ("tenant_id", bool((payload.get("tenant_id") or "").strip())),
            (
                "token",
                bool(
                    isinstance(payload.get("token"), dict)
                    and payload["token"].get("access_token")
                    and payload["token"].get("refresh_token")
                ),
            ),
        )
        if not ok
    ]
    if missing:
        print("Refusing to save incomplete Xero auth. Missing:", ", ".join(missing))
        print("Settings file exists:", SETTINGS_PATH.is_file(), SETTINGS_PATH)
        print("Token file exists:", TOKEN_PATH.is_file(), TOKEN_PATH)
        return 1

    db.save_integration_settings(STORAGE_KEY, payload)
    print("Saved Xero authentication to PostgreSQL integration_settings.xero")
    print("client_id:", (payload.get("client_id") or "")[:8] + "…")
    print("tenant_id:", (payload.get("tenant_id") or "")[:8] + "…")
    print("token.access_token: present")
    print("token.refresh_token: present")
    print("JSON files were not deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
