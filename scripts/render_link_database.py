#!/usr/bin/env python3
"""Link Render PostgreSQL to the web service by setting DATABASE_URL.

Requires RENDER_API_KEY (Render Dashboard → Account Settings → API Keys).

Usage:
  RENDER_API_KEY=rnd_... python scripts/render_link_database.py
  RENDER_API_KEY=rnd_... python scripts/render_link_database.py --deploy
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://api.render.com/v1"
SERVICE_NAME = "japanese-removals-bookings"
DATABASE_NAME = "japanese-removals-db"
CLI_CONFIG_PATH = os.path.expanduser("~/.render/cli.yaml")


def _load_api_key() -> str:
    key = (os.environ.get("RENDER_API_KEY") or "").strip()
    if key:
        return key
    path = Path(CLI_CONFIG_PATH)
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("key:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _request(method: str, path: str, api_key: str, payload: dict | None = None) -> object:
    url = API_BASE + path
    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer {0}".format(api_key),
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit("Render API {0} {1} failed ({2}): {3}".format(method, path, exc.code, detail))


def _unwrap(items: list) -> list:
    """Render list endpoints return [{cursor, service/postgres}, ...]."""
    out = []
    for item in items or []:
        if isinstance(item, dict):
            for key in ("service", "postgres", "postgresInstance", "envVar"):
                if key in item and isinstance(item[key], dict):
                    out.append(item[key])
                    break
            else:
                out.append(item)
    return out


def _find_by_name(items: list, name: str, label: str) -> dict:
    matches = [item for item in items if (item.get("name") or "") == name]
    if not matches:
        names = [item.get("name") for item in items]
        raise SystemExit("{0} '{1}' not found. Found: {2}".format(label, name, names))
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Set DATABASE_URL on Render web service")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Trigger a deploy after updating DATABASE_URL",
    )
    args = parser.parse_args()

    api_key = _load_api_key()
    if not api_key:
        print("Set RENDER_API_KEY or run `render login` (Dashboard → Account Settings → API Keys).")
        return 1

    services = _unwrap(_request("GET", "/services?limit=100", api_key))
    postgres_list = _unwrap(_request("GET", "/postgres?limit=100", api_key))

    service = _find_by_name(services, SERVICE_NAME, "Service")
    database = _find_by_name(postgres_list, DATABASE_NAME, "Database")

    service_id = service.get("id")
    postgres_id = database.get("id")
    if not service_id or not postgres_id:
        raise SystemExit("Missing service or database id from Render API.")

    conn = _request("GET", "/postgres/{0}/connection-info".format(postgres_id), api_key)
    db_url = (conn.get("internalConnectionString") or "").strip()
    if not db_url.startswith("postgres"):
        raise SystemExit("Could not read internalConnectionString from Render.")

    _request(
        "PUT",
        "/services/{0}/env-vars/DATABASE_URL".format(service_id),
        api_key,
        {"value": db_url},
    )
    print("Set DATABASE_URL on {0} ({1})".format(SERVICE_NAME, service_id))
    print("Database: {0} ({1})".format(DATABASE_NAME, postgres_id))
    print("URL host:", db_url.split("@")[-1].split("/")[0])

    if args.deploy:
        _request("POST", "/services/{0}/deploys".format(service_id), api_key, {})
        print("Triggered deploy for {0}".format(SERVICE_NAME))
    else:
        print("Run with --deploy to redeploy, or restart the service in Render Dashboard.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
