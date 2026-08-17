#!/usr/bin/env python3
"""Production verification — editable Finish time on Edit Booking."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from time import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS_DIR = ROOT / "test_results" / "production"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

PRODUCTION_URL = os.environ.get(
    "APP_BASE_URL", "https://japanese-removals-bookings.onrender.com"
).rstrip("/")

CLI_CONFIG_PATH = Path.home() / ".render" / "cli.yaml"
SERVICE_NAME = "japanese-removals-bookings"
TEST_MARKER = "PROD-FINISH-TIME-TEST"


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
    return out


def _load_staff_credentials_from_render() -> tuple[str, str]:
    api_key = _load_render_api_key()
    if not api_key:
        return "", ""
    services = _unwrap(_render_api("/services?limit=100", api_key))
    service = next((s for s in services if s.get("name") == SERVICE_NAME), None)
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


def _latest_deploy_status() -> dict:
    api_key = _load_render_api_key()
    if not api_key:
        return {"status": "unknown", "detail": "No Render API key"}
    services = _unwrap(_render_api("/services?limit=100", api_key))
    service = next((s for s in services if s.get("name") == SERVICE_NAME), None)
    if not service:
        return {"status": "unknown", "detail": "Service not found"}
    deploys = _unwrap(
        _render_api("/services/{0}/deploys?limit=1".format(service["id"]), api_key)
    )
    if not deploys:
        return {"status": "unknown", "detail": "No deploys"}
    deploy = deploys[0]
    return {
        "status": deploy.get("status", "unknown"),
        "commit": (deploy.get("commit") or {}).get("id", "")[:8],
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
    ) -> tuple[int, str, str, dict]:
        url = PRODUCTION_URL + path
        headers = {"User-Agent": "production-finish-time-test/1.0"}
        body = None
        if data is not None:
            pairs: list[tuple[str, str]] = []
            for key, value in data.items():
                if isinstance(value, list):
                    for item in value:
                        pairs.append((key, item))
                else:
                    pairs.append((key, str(value)))
            body = urllib.parse.urlencode(pairs).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=120)
            html = resp.read().decode("utf-8", errors="replace")
            return resp.status, html, resp.geturl(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            html = exc.read().decode("utf-8", errors="replace")
            hdrs = dict(exc.headers)
            if follow and exc.code in (301, 302, 303, 307, 308):
                location = hdrs.get("Location") or ""
                if location.startswith("/"):
                    location = PRODUCTION_URL + location
                if location:
                    sub_status, sub_html, sub_url, sub_hdrs = self.request(
                        "GET", location.replace(PRODUCTION_URL, ""), None, follow=True
                    )
                    return sub_status, sub_html, sub_url, sub_hdrs
            return exc.code, html, url, hdrs


def _login(client: ProductionClient, username: str, password: str) -> bool:
    status, html, _, _ = client.request(
        "POST",
        "/login",
        {"username": username, "password": password, "next": "/dashboard"},
    )
    return status == 200 and "Staff login" not in html


def _edit_form_from_get(html: str) -> dict:
    form: dict = {"action": "save"}
    for match in re.finditer(
        r'<input[^>]+name="([^"]+)"[^>]*value="([^"]*)"',
        html,
    ):
        name, value = match.group(1), match.group(2)
        if name in ("secret_key", "webhook_secret", "action"):
            continue
        if name == "crew":
            form.setdefault("crew", []).append(value)
        else:
            form[name] = value
    for match in re.finditer(
        r'<textarea[^>]+name="([^"]+)"[^>]*>(.*?)</textarea>',
        html,
        re.S,
    ):
        form[match.group(1)] = re.sub(r"<[^>]+>", "", match.group(2)).strip()
    for match in re.finditer(
        r'<select[^>]+name="([^"]+)"[^>]*>.*?<option[^>]*selected[^>]*value="([^"]*)"',
        html,
        re.S,
    ):
        form[match.group(1)] = match.group(2)
    if 'name="gst_enabled"' in html and re.search(
        r'name="gst_enabled"[^>]*checked', html
    ):
        form["gst_enabled"] = "on"
    form["action"] = "save"
    return form


def _input_value(html: str, name: str) -> str:
    match = re.search(
        r'<input[^>]+name="{0}"[^>]+value="([^"]*)"'.format(re.escape(name)),
        html,
    )
    if match:
        return match.group(1)
    match = re.search(
        r'<input[^>]+value="([^"]*)"[^>]+name="{0}"'.format(re.escape(name)),
        html,
    )
    return match.group(1) if match else ""


def _finish_time_input(html: str) -> str | None:
    match = re.search(
        r'<input[^>]+type="time"[^>]+name="finish_time"[^>]*>',
        html,
    )
    if not match:
        match = re.search(
            r'<input[^>]+name="finish_time"[^>]+type="time"[^>]*>',
            html,
        )
    return match.group(0) if match else None


def _live_bank_total(html: str) -> str:
    match = re.search(r'id="live-bank-total"[^>]*>([^<]+)<', html)
    return match.group(1).strip() if match else ""


def _find_booking_id(client: ProductionClient, customer_name: str) -> int | None:
    status, html, _, _ = client.request(
        "GET", "/bookings/search?q=" + urllib.parse.quote(customer_name)
    )
    if status != 200:
        return None
    idx = html.find(customer_name)
    if idx < 0:
        return None
    window = html[max(0, idx - 400) : idx + 200]
    match = re.search(r"/bookings/(\d+)", window)
    return int(match.group(1)) if match else None


def _create_test_booking(client: ProductionClient, customer_name: str, move_date: str) -> int | None:
    form = {
        "action": "save",
        "customer_name": customer_name,
        "phone": "0400000099",
        "email": "prod-finish-time@example.com",
        "pickup_address": "1 Finish Test St, Perth WA 6000",
        "delivery_address": "2 Finish Test Ave, Fremantle WA 6160",
        "move_date": move_date,
        "status": "Confirmed",
        "start_time": "08:00",
        "duration_hours": "3",
        "num_movers": "2",
        "notes": TEST_MARKER,
        "hourly_rate": "180",
        "callout_fee": "90",
        "gst_enabled": "on",
        "payment_status": "Unpaid",
        "invoice_status": "",
        "truck_assigned": "",
        "double_booking_override_confirm": "on",
    }
    status, html, url, _ = client.request("POST", "/bookings/new", form)
    if status != 200:
        return None
    match = re.search(r"Booking saved \(reference #(\d+)\)", html)
    if match:
        return int(match.group(1))
    return _find_booking_id(client, customer_name)


def _first_existing_booking_id(client: ProductionClient) -> int | None:
    status, html, _, _ = client.request("GET", "/dashboard")
    if status != 200:
        return None
    match = re.search(r'href="/bookings/(\d+)/edit"', html)
    return int(match.group(1)) if match else None


def main() -> int:
    results: list[dict] = []
    username, password = _load_staff_credentials_from_render()
    if not username or not password:
        username = (os.environ.get("STAFF_USERNAME") or "").strip()
        password = (os.environ.get("STAFF_PASSWORD") or "").strip()
    if not username or not password:
        print("FAIL: Set STAFF_USERNAME/STAFF_PASSWORD or Render API key")
        return 1

    deploy = _latest_deploy_status()
    print("Latest deploy:", deploy)

    client = ProductionClient()
    if not _login(client, username, password):
        print("FAIL: Could not log in to production")
        return 1

    customer = "{0}-{1}".format(TEST_MARKER, int(time()))
    move_date = (date.today() + timedelta(days=90)).isoformat()
    booking_id = _create_test_booking(client, customer, move_date)
    if not booking_id:
        print("FAIL: Could not create test booking")
        return 1

    status, html, _, _ = client.request("GET", "/bookings/{0}/edit".format(booking_id))
    finish_input = _finish_time_input(html)
    finish_val = _input_value(html, "finish_time")
    duration_val = _input_value(html, "pricing_duration_hours") or _input_value(
        html, "duration_hours"
    )

    ok1 = status == 200 and finish_input is not None
    results.append(
        {
            "check": "Edit Booking has editable Finish time input",
            "pass": ok1,
            "detail": "finish_time={0}".format(finish_val),
        }
    )

    ok2 = finish_val == "11:00" and duration_val in ("3", "3.0")
    results.append(
        {
            "check": "Initial Finish time and Duration from 3h booking",
            "pass": ok2,
            "detail": "finish={0} duration={1}".format(finish_val, duration_val),
        }
    )

    calc_form = _edit_form_from_get(html)
    calc_form["finish_time"] = "13:00"
    calc_form["duration_hours"] = duration_val or "3"
    status_calc, calc_html, _, _ = client.request(
        "POST", "/bookings/invoice/preview-calculate", calc_form
    )
    calc_ok = status_calc == 200
    calc_total = ""
    calc_hours = None
    if calc_ok:
        try:
            payload = json.loads(calc_html)
            calc_total = payload.get("bank_total_display", "")
            calc_hours = payload.get("hours")
            calc_ok = calc_hours == 5.0 and "$990.00" in calc_total
        except json.JSONDecodeError:
            calc_ok = False
    results.append(
        {
            "check": "Invoice recalculates after Finish time change (preview)",
            "pass": calc_ok,
            "detail": "hours={0} total={1}".format(calc_hours, calc_total),
        }
    )

    save_form = _edit_form_from_get(html)
    save_form["finish_time"] = "13:00"
    save_form["status"] = "Completed"
    save_form["double_booking_override_confirm"] = "on"
    status_save, _, save_url, _ = client.request(
        "POST", "/bookings/{0}/edit".format(booking_id), save_form, follow=True
    )
    status_after, html_after, _, _ = client.request(
        "GET", "/bookings/{0}/edit".format(booking_id)
    )
    saved_finish = _input_value(html_after, "finish_time")
    saved_duration = _input_value(html_after, "pricing_duration_hours") or _input_value(
        html_after, "duration_hours"
    )
    ok4 = saved_finish == "13:00" and saved_duration in ("5", "5.0")
    results.append(
        {
            "check": "Save persists Finish time and updated Duration",
            "pass": ok4,
            "detail": "finish={0} duration={1} save_status={2}".format(
                saved_finish, saved_duration, status_save
            ),
        }
    )

    ok3 = ok4 and "$990.00" in _live_bank_total(html_after)
    results.append(
        {
            "check": "Invoice total reflects 5 hours after save",
            "pass": ok3,
            "detail": _live_bank_total(html_after),
        }
    )

    existing_id = _first_existing_booking_id(client)
    existing_ok = False
    existing_detail = "no booking on dashboard"
    if existing_id and existing_id != booking_id:
        st, existing_html, _, _ = client.request(
            "GET", "/bookings/{0}/edit".format(existing_id)
        )
        before_finish = _input_value(existing_html, "finish_time")
        existing_ok = (
            st == 200
            and _finish_time_input(existing_html) is not None
            and "Internal Server Error" not in existing_html
        )
        st2, html2, _, _ = client.request(
            "GET", "/bookings/{0}/edit".format(existing_id)
        )
        after_finish = _input_value(html2, "finish_time")
        existing_ok = existing_ok and before_finish == after_finish
        existing_detail = "booking #{0} finish={1}".format(existing_id, before_finish)
    results.append(
        {
            "check": "Existing bookings load unchanged (read-only verify)",
            "pass": existing_ok,
            "detail": existing_detail,
        }
    )

    out_path = RESULTS_DIR / "finish_time_verify_results.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    passed = sum(1 for r in results if r["pass"])
    for r in results:
        print("{0}: {1}".format("PASS" if r["pass"] else "FAIL", r["check"]))
        print("  ", r["detail"])
    print("\n{0}/{1} checks passed".format(passed, len(results)))
    print("Results:", out_path)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
