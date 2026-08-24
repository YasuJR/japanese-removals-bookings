#!/usr/bin/env python3
"""Production verification for Daily Jobs deployment (read-only)."""

from __future__ import annotations

import json
import os
import re
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
SERVICE_NAME = "japanese-removals-bookings"
VERIFY_DATE = os.environ.get("DAILY_JOBS_VERIFY_DATE", "2026-08-18")
CLI_CONFIG_PATH = Path.home() / ".render" / "cli.yaml"
RESULTS_PATH = ROOT / "test_results" / "production" / "daily_jobs_verify_results.json"


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


def _unwrap(items: object) -> list:
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if isinstance(item, dict):
            for key in ("service", "envVar", "deploy"):
                if key in item:
                    out.append(item[key])
                    break
            else:
                out.append(item)
    return out


def _load_staff_credentials_from_render() -> tuple[str, str]:
    api_key = _load_render_api_key()
    if not api_key:
        return "", ""
    try:
        services = _unwrap(_render_api("/services?limit=100", api_key))
    except urllib.error.HTTPError:
        return "", ""
    service = next((s for s in services if s.get("name") == SERVICE_NAME), None)
    if not service:
        return "", ""
    envs = _unwrap(
        _render_api("/services/{0}/env-vars?limit=100".format(service["id"]), api_key)
    )
    values = {e.get("key"): e.get("value", "") for e in envs}
    return (
        (values.get("STAFF_USERNAME") or "").strip(),
        (values.get("STAFF_PASSWORD") or "").strip(),
    )


def _latest_deploy_status() -> dict:
    api_key = _load_render_api_key()
    if not api_key:
        return {"status": "unknown", "detail": "No Render API key"}
    try:
        services = _unwrap(_render_api("/services?limit=100", api_key))
    except urllib.error.HTTPError as exc:
        return {"status": "unknown", "detail": "Render API {0}".format(exc.code)}
    service = next((s for s in services if s.get("name") == SERVICE_NAME), None)
    if not service:
        return {"status": "unknown", "detail": "Service not found"}
    deploys = _unwrap(
        _render_api("/services/{0}/deploys?limit=1".format(service["id"]), api_key)
    )
    if not deploys:
        return {"status": "unknown", "detail": "No deploys"}
    deploy = deploys[0]
    commit = deploy.get("commit") or {}
    return {
        "status": deploy.get("status", "unknown"),
        "commit": commit.get("id", ""),
        "detail": deploy.get("status", ""),
    }


class ProductionClient:
    def __init__(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        *,
        follow: bool = True,
    ) -> tuple[int, str, str]:
        url = PRODUCTION_URL + path
        headers = {"User-Agent": "production-daily-jobs-verify/1.0"}
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=120)
            html = resp.read().decode("utf-8", errors="replace")
            return resp.status, html, resp.geturl()
        except urllib.error.HTTPError as exc:
            html = exc.read().decode("utf-8", errors="replace")
            hdrs = dict(exc.headers)
            if follow and exc.code in (301, 302, 303, 307, 308):
                location = hdrs.get("Location") or ""
                if location.startswith("/"):
                    location = PRODUCTION_URL + location
                if location:
                    sub_status, sub_html, sub_url = self.request(
                        "GET",
                        location.replace(PRODUCTION_URL, ""),
                        None,
                        follow=True,
                    )
                    return sub_status, sub_html, sub_url
            return exc.code, html, url


def _login(client: ProductionClient, username: str, password: str) -> bool:
    status, html, _ = client.request(
        "POST",
        "/login",
        {"username": username, "password": password, "next": "/calendar"},
    )
    return status == 200 and "Staff login" not in html


def _parse_ampm_to_minutes(text: str) -> int:
    match = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)", (text or "").strip(), re.I)
    if not match:
        return 0
    hour = int(match.group(1)) % 12
    minute = int(match.group(2))
    if match.group(3).upper() == "PM":
        hour += 12
    return hour * 60 + minute


def _extract_job_cards(html: str) -> list[dict]:
    cards = re.findall(
        r'<article class="daily-job-card"[^>]*>(.*?)</article>',
        html,
        re.S,
    )
    jobs = []
    for card in cards:
        label = re.search(r'class="daily-job-label"[^>]*>([^<]+)<', card)
        customer = re.search(r'class="daily-job-customer"[^>]*>([^<]+)<', card)
        time_range = re.search(r'class="daily-job-time-range"[^>]*>([^<]+)<', card)
        if not time_range:
            time_range = re.search(r'class="daily-job-time"[^>]*>([^<]+)<', card)
        start_text = ""
        if time_range:
            start_text = (time_range.group(1).split("–")[0].strip())
        jobs.append(
            {
                "label": label.group(1).strip() if label else "",
                "customer": customer.group(1).strip() if customer else "",
                "start_display": start_text,
                "start_minutes": _parse_ampm_to_minutes(start_text),
            }
        )
    return jobs


def main() -> int:
    results: list[dict] = []
    health: dict = {}
    try:
        with urllib.request.urlopen(PRODUCTION_URL + "/health", timeout=60) as resp:
            health = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        health = {"status": "error", "detail": str(exc)}

    health_ok = health.get("status") == "ok"
    results.append(
        {
            "name": "Production health check",
            "pass": health_ok,
            "detail": json.dumps(health),
        }
    )

    deploy = _latest_deploy_status()
    results.append(
        {
            "name": "Render deploy live",
            "pass": deploy.get("status") == "live",
            "detail": json.dumps(deploy),
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
                "name": "Calendar daily navigation",
                "pass": False,
                "detail": "Skipped — no staff credentials",
            }
        )
        results.append(
            {
                "name": "Daily Jobs page for {0}".format(VERIFY_DATE),
                "pass": False,
                "detail": "Skipped — no staff credentials",
            }
        )
        results.append(
            {
                "name": "JOB order by start time",
                "pass": False,
                "detail": "Skipped — no staff credentials",
            }
        )
    else:
        client = ProductionClient()
        logged_in = _login(client, username, password)
        results.append(
            {
                "name": "Staff login",
                "pass": logged_in,
                "detail": "OK" if logged_in else "Login failed",
            }
        )

        cal_status, cal_html, _ = client.request(
            "GET", "/calendar?view=month&year=2026&month=8"
        )
        calendar_ok = (
            logged_in
            and cal_status == 200
            and 'class="calendar-day"' in cal_html
            and "calendar.js" in cal_html
            and "calendar-day-panel" not in cal_html
        )
        results.append(
            {
                "name": "Calendar page ready for daily navigation",
                "pass": calendar_ok,
                "detail": "status={0}".format(cal_status),
            }
        )

        daily_status, daily_html, daily_url = client.request(
            "GET", "/calendar/daily/{0}".format(VERIFY_DATE)
        )
        daily_ok = (
            logged_in
            and daily_status == 200
            and "Daily Jobs" in daily_html
            and daily_url.endswith("/calendar/daily/{0}".format(VERIFY_DATE))
        )
        results.append(
            {
                "name": "Daily Jobs page for {0}".format(VERIFY_DATE),
                "pass": daily_ok,
                "detail": daily_url,
            }
        )

        jobs = _extract_job_cards(daily_html)
        labels = [job["label"] for job in jobs]
        starts = [job["start_minutes"] for job in jobs]
        order_ok = (
            daily_ok
            and len(jobs) >= 2
            and labels[:2] == ["JOB 1", "JOB 2"]
            and starts == sorted(starts)
        )
        results.append(
            {
                "name": "JOB order by start time",
                "pass": order_ok,
                "detail": json.dumps(jobs),
            }
        )

    all_pass = all(item["pass"] for item in results)
    payload = {
        "production_url": PRODUCTION_URL,
        "verify_date": VERIFY_DATE,
        "health": health,
        "deploy": deploy,
        "results": results,
        "all_pass": all_pass,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for item in results:
        status = "PASS" if item["pass"] else "FAIL"
        print("{0}: {1} — {2}".format(status, item["name"], item["detail"][:200]))
    print("\nCommit:", health.get("git_commit", deploy.get("commit", "")))
    print("Results:", RESULTS_PATH)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
