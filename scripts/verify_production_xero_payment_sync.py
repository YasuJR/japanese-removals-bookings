#!/usr/bin/env python3
"""Production verification — Xero bank-transfer payment sync (safe / no test bookings)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PRODUCTION_URL = os.environ.get(
    "APP_BASE_URL", "https://japanese-removals-bookings.onrender.com"
).rstrip("/")
WEB_SERVICE_NAME = "japanese-removals-bookings"
CRON_SERVICE_NAME = "xero-payment-sync"
EXPECTED_SCHEDULE = "*/15 * * * *"
REQUIRED_XERO_ENV = (
    "XERO_CLIENT_ID",
    "XERO_CLIENT_SECRET",
    "XERO_TENANT_ID",
    "XERO_TOKEN_JSON",
    "XERO_REDIRECT_URI",
)
CLI_CONFIG_PATH = Path.home() / ".render" / "cli.yaml"
RESULTS_PATH = ROOT / "test_results" / "production" / "xero_payment_sync_verify_results.json"


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
            for key in ("service", "envVar", "deploy"):
                if key in item and isinstance(item[key], dict):
                    out.append(item[key])
                    break
            else:
                out.append(item)
        else:
            out.append(item)
    return out


def _load_staff_credentials_from_render() -> tuple[str, str]:
    api_key = _load_render_api_key()
    if not api_key:
        return "", ""
    services = _unwrap(_render_api("/services?limit=100", api_key))
    service = next((s for s in services if s.get("name") == WEB_SERVICE_NAME), None)
    if not service:
        return "", ""
    envs = _unwrap(
        _render_api("/services/{0}/env-vars?limit=100".format(service["id"]), api_key)
    )
    values = {ev.get("key"): ev.get("value", "") for ev in envs}
    return (
        (values.get("STAFF_USERNAME") or "").strip(),
        (values.get("STAFF_PASSWORD") or "").strip(),
    )


class ProductionClient:
    def __init__(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(self, method: str, path: str, data: dict | None = None):
        url = PRODUCTION_URL + path
        body = None
        headers = {"User-Agent": "xero-payment-sync-verify"}
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=60) as resp:
                return resp.getcode(), resp.read().decode("utf-8", "replace"), resp.geturl()
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", "replace") if exc.fp else ""
            return exc.code, payload, path


def _login(client: ProductionClient, username: str, password: str) -> bool:
    status, html, _ = client.request(
        "POST",
        "/login",
        {"username": username, "password": password},
    )
    return status == 200 and "Staff login" not in html


def _env_presence(envs: list) -> dict:
    values = {ev.get("key"): bool(str(ev.get("value") or "").strip()) for ev in envs}
    present = [key for key in REQUIRED_XERO_ENV if values.get(key)]
    missing = [key for key in REQUIRED_XERO_ENV if not values.get(key)]
    return {"present": present, "missing": missing}


def main() -> int:
    results = []
    health = {}
    try:
        with urllib.request.urlopen(PRODUCTION_URL + "/health", timeout=60) as resp:
            health = json.loads(resp.read().decode("utf-8"))
            health_code = resp.status
    except Exception as exc:
        health = {"status": "error", "detail": str(exc)}
        health_code = 0

    results.append(
        {
            "name": "GET /health",
            "pass": health_code == 200 and health.get("status") == "ok",
            "detail": {
                "http": health_code,
                "git_commit": health.get("git_commit"),
                "status": health.get("status"),
            },
        }
    )

    api_key = _load_render_api_key()
    cron_detail = {"status": "unknown"}
    env_detail = {"web": {}, "cron": {}}
    if not api_key:
        results.append(
            {
                "name": "Render cron {0} every 15 minutes".format(CRON_SERVICE_NAME),
                "pass": False,
                "detail": "No Render API key",
            }
        )
        results.append(
            {
                "name": "Production Xero environment variables",
                "pass": False,
                "detail": "No Render API key",
            }
        )
    else:
        services = _unwrap(_render_api("/services?limit=100", api_key))
        cron = next((s for s in services if s.get("name") == CRON_SERVICE_NAME), None)
        web = next((s for s in services if s.get("name") == WEB_SERVICE_NAME), None)
        schedule = ""
        if cron:
            schedule = (
                (cron.get("serviceDetails") or {}).get("schedule")
                or cron.get("schedule")
                or ""
            )
        cron_detail = {
            "found": bool(cron),
            "id": (cron or {}).get("id"),
            "schedule": schedule,
            "suspended": (cron or {}).get("suspended"),
        }
        results.append(
            {
                "name": "Render cron {0} every 15 minutes".format(CRON_SERVICE_NAME),
                "pass": bool(cron) and schedule == EXPECTED_SCHEDULE,
                "detail": cron_detail,
            }
        )
        web_env = _unwrap(
            _render_api("/services/{0}/env-vars?limit=100".format(web["id"]), api_key)
        ) if web else []
        cron_env = _unwrap(
            _render_api("/services/{0}/env-vars?limit=100".format(cron["id"]), api_key)
        ) if cron else []
        web_presence = _env_presence(web_env)
        cron_presence = _env_presence(cron_env)
        env_detail = {"web": web_presence, "cron": cron_presence}
        results.append(
            {
                "name": "Production Xero environment variables",
                "pass": not web_presence["missing"] and not cron_presence["missing"],
                "detail": env_detail,
            }
        )

    username = (
        os.environ.get("PRODUCTION_TEST_USERNAME")
        or os.environ.get("STAFF_USERNAME")
        or ""
    ).strip()
    password = (
        os.environ.get("PRODUCTION_TEST_PASSWORD")
        or os.environ.get("STAFF_PASSWORD")
        or ""
    ).strip()
    if not username or not password:
        render_user, render_pass = _load_staff_credentials_from_render()
        username = username or render_user
        password = password or render_pass

    if not username or not password:
        results.append(
            {
                "name": "Dashboard Xero payment sync controls",
                "pass": False,
                "detail": "Skipped — no staff credentials",
            }
        )
    else:
        client = ProductionClient()
        logged_in = _login(client, username, password)
        status, html, url = client.request("GET", "/dashboard")
        dashboard_ok = (
            logged_in
            and status == 200
            and "Sync Xero Payments" in html
            and "Last Xero Sync:" in html
            and "Staff login" not in html
        )
        results.append(
            {
                "name": "Dashboard Xero payment sync controls",
                "pass": dashboard_ok,
                "detail": {
                    "login": logged_in,
                    "http": status,
                    "url": url,
                    "has_sync_button": "Sync Xero Payments" in html,
                    "has_last_sync": "Last Xero Sync:" in html,
                },
            }
        )

    payload = {
        "production_url": PRODUCTION_URL,
        "health": health,
        "results": results,
        "all_pass": all(item["pass"] for item in results),
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for item in results:
        status = "PASS" if item["pass"] else "FAIL"
        detail = item["detail"]
        if not isinstance(detail, str):
            detail = json.dumps(detail)
        print("{0}: {1} — {2}".format(status, item["name"], detail[:300]))
    print("\nCommit:", health.get("git_commit", ""))
    print("Results:", RESULTS_PATH)
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
