#!/usr/bin/env python3
"""Production-safe verification for paste parser deployment (no booking writes)."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PRODUCTION_URL = os.environ.get(
    "APP_BASE_URL", "https://japanese-removals-bookings.onrender.com"
).rstrip("/")
EXPECTED_COMMIT_PREFIX = os.environ.get("EXPECTED_GIT_COMMIT", "7d7c1b3")
SERVICE_NAME = "japanese-removals-bookings"
TEST5 = (
    "Hi, moving from Cannington to Innaloo sometime next week. "
    "Please call me on 0412 111 222."
)
CLI_CONFIG_PATH = Path.home() / ".render" / "cli.yaml"


def _load_render_api_key() -> str:
    key = (os.environ.get("RENDER_API_KEY") or "").strip()
    if key:
        return key
    if CLI_CONFIG_PATH.is_file():
        for line in CLI_CONFIG_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("key:"):
                return line.split(":", 1)[1].strip()
    return ""


def _render_api(path: str, api_key: str) -> object:
    req = urllib.request.Request(
        "https://api.render.com/v1" + path,
        headers={
            "Authorization": "Bearer {0}".format(api_key),
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _unwrap(items: list) -> list:
    out = []
    for item in items or []:
        if isinstance(item, dict):
            for key in ("service", "deploy", "postgres"):
                if key in item and isinstance(item[key], dict):
                    out.append(item[key])
                    break
            else:
                out.append(item)
    return out


def check_health() -> dict:
    with urllib.request.urlopen(PRODUCTION_URL + "/health", timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    ok = (
        resp.status == 200
        and body.get("status") == "ok"
        and body.get("database") == "postgres"
        and str(body.get("git_commit", "")).startswith(EXPECTED_COMMIT_PREFIX)
    )
    return {
        "name": "Production /health",
        "pass": ok,
        "detail": body,
    }


def check_render_deploy(api_key: str) -> dict:
    services = _unwrap(_render_api("/services?limit=100", api_key))
    service = next((s for s in services if s.get("name") == SERVICE_NAME), None)
    if not service:
        return {"name": "Render deploy", "pass": False, "detail": "Service not found"}
    deploys = _unwrap(_render_api("/services/{0}/deploys?limit=1".format(service["id"]), api_key))
    deploy = deploys[0] if deploys else {}
    commit = ((deploy.get("commit") or {}).get("id") or "")[:12]
    ok = deploy.get("status") == "live" and commit.startswith(EXPECTED_COMMIT_PREFIX)
    return {
        "name": "Render deploy",
        "pass": ok,
        "detail": {
            "status": deploy.get("status"),
            "commit": commit,
            "finishedAt": deploy.get("finishedAt"),
        },
    }


def check_new_booking_page() -> dict:
    req = urllib.request.Request(
        PRODUCTION_URL + "/bookings/new",
        headers={"User-Agent": "production-verify/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        final_url = resp.geturl()
        html = resp.read().decode("utf-8", errors="replace")
    ok = resp.status == 200 and (
        "Paste Customer Information" in html or "Staff login" in html
    )
    return {
        "name": "New Booking page reachable",
        "pass": ok,
        "detail": {"status": resp.status, "final_url": final_url},
    }


def check_local_parser_test5() -> dict:
    from integrations import enquiry_parser

    ref = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo("Australia/Perth"))
    parsed = enquiry_parser.parse_pasted_text(TEST5, reference=ref)
    ok = (
        parsed.get("phone") == "0412 111 222"
        and parsed.get("move_date") == ""
        and "Date could not be confidently determined" in parsed.get("warnings", [])
        and "Full pickup street address not found" in parsed.get("warnings", [])
    )
    return {
        "name": "Parser logic (TEST 5, deployed commit code)",
        "pass": ok,
        "detail": {
            "phone": parsed.get("phone"),
            "move_date": parsed.get("move_date"),
            "warnings": parsed.get("warnings"),
        },
    }


def check_production_analyse_post() -> dict:
    username = (os.environ.get("PRODUCTION_TEST_USERNAME") or os.environ.get("STAFF_USERNAME") or "").strip()
    password = (os.environ.get("PRODUCTION_TEST_PASSWORD") or os.environ.get("STAFF_PASSWORD") or "").strip()
    if not username or not password:
        return {
            "name": "Production Analyse POST (authenticated)",
            "pass": None,
            "detail": "Skipped — set PRODUCTION_TEST_USERNAME and PRODUCTION_TEST_PASSWORD to run live POST test.",
        }

    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def post(url: str, data: dict) -> tuple[str, str, int]:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "User-Agent": "production-verify/1.0",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with opener.open(req, timeout=60) as resp:
            return resp.read().decode("utf-8", errors="replace"), resp.geturl(), resp.status

    post(
        PRODUCTION_URL + "/login",
        {"username": username, "password": password, "next": "/bookings/new"},
    )
    html, final_url, status = post(
        PRODUCTION_URL + "/bookings/new",
        {"action": "analyse_paste", "paste_text": TEST5},
    )
    ok = (
        status == 200
        and "Extracted details" in html
        and "Check these details" in html
        and "Date could not be confidently determined" in html
        and not re.search(r"Booking saved \(reference #", html)
    )
    return {
        "name": "Production Analyse POST (authenticated)",
        "pass": ok,
        "detail": {
            "status": status,
            "final_url": final_url,
            "has_warnings": "Check these details" in html,
            "saved_booking": bool(re.search(r"Booking saved \(reference #", html)),
        },
    }


def check_database_readonly(api_key: str) -> dict:
    postgres_list = _unwrap(_render_api("/postgres?limit=100", api_key))
    database = next((d for d in postgres_list if d.get("name") == "japanese-removals-db"), None)
    if not database:
        return {"name": "Database status", "pass": False, "detail": "Database not found"}
    detail = _render_api("/postgres/{0}".format(database["id"]), api_key)
    ok = detail.get("status") == "available"
    return {
        "name": "Database status",
        "pass": ok,
        "detail": {
            "name": detail.get("name"),
            "status": detail.get("status"),
            "note": "Read-only Render API check — no booking rows modified.",
        },
    }


def main() -> int:
    results = []
    results.append(check_health())
    results.append(check_new_booking_page())
    results.append(check_local_parser_test5())
    results.append(check_production_analyse_post())

    api_key = _load_render_api_key()
    if api_key:
        results.append(check_render_deploy(api_key))
        results.append(check_database_readonly(api_key))
    else:
        results.append(
            {
                "name": "Render deploy",
                "pass": None,
                "detail": "Skipped — RENDER_API_KEY or render login not available.",
            }
        )

    failed = 0
    skipped = 0
    for item in results:
        status = "PASS" if item["pass"] else ("SKIP" if item["pass"] is None else "FAIL")
        if item["pass"] is False:
            failed += 1
        if item["pass"] is None:
            skipped += 1
        print("{0}: {1}".format(status, item["name"]))
        print("  {0}".format(item["detail"]))
        print()

    actionable = [r for r in results if r["pass"] is not None]
    passed = sum(1 for r in actionable if r["pass"])
    print("{0}/{1} checks passed ({2} skipped)".format(passed, len(actionable), skipped))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
