#!/usr/bin/env python3
"""Production-safe test: create test booking, cancel it, verify status."""

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
            for key in ("service", "envVar"):
                if key in item and isinstance(item[key], dict):
                    out.append(item[key])
                    break
            else:
                out.append(item)
    return out


TEST_MARKER = "PROD-CANCEL-TEST"


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
        headers = {"User-Agent": "production-cancel-test/1.0"}
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


def _new_booking_form(client: ProductionClient, customer_name: str, move_date: str) -> dict:
    return {
        "action": "save",
        "customer_name": customer_name,
        "phone": "0400000001",
        "email": "prod-cancel-test@example.com",
        "pickup_address": "1 Cancel Test St, Perth WA 6000",
        "delivery_address": "2 Cancel Test Ave, Fremantle WA 6160",
        "move_date": move_date,
        "status": "Confirmed",
        "start_time": "04:30",
        "duration_hours": "1",
        "num_movers": "1",
        "notes": TEST_MARKER + " — safe to leave cancelled",
        "hourly_rate": "1",
        "callout_fee": "0",
        "gst_enabled": "on",
        "payment_status": "Unpaid",
        "invoice_status": "",
        "truck_assigned": "",
        "double_booking_override_confirm": "on",
    }


def _extract_booking_id_from_html(html: str) -> int | None:
    match = re.search(r"Booking saved \(reference #(\d+)\)", html)
    if match:
        return int(match.group(1))
    return None


def _extract_booking_id_from_redirect(location: str) -> int | None:
    for pattern in (
        r"/bookings/(\d+)/edit",
        r"/bookings/(\d+)",
        r"booking_id=(\d+)",
    ):
        match = re.search(pattern, location)
        if match:
            return int(match.group(1))
    return None


def _find_test_booking_id(client: ProductionClient, customer_name: str) -> int | None:
    for path in ("/bookings/search?q=" + urllib.parse.quote(customer_name), "/bookings/all"):
        status, html, _, _ = client.request("GET", path)
        if status != 200:
            continue
        for pattern in (
            r'href="/bookings/(\d+)/edit"',
            r'href="/bookings/(\d+)"',
        ):
            if customer_name not in html:
                continue
            # find booking id near customer name
            idx = html.find(customer_name)
            window = html[max(0, idx - 400) : idx + 200]
            match = re.search(r"/bookings/(\d+)", window)
            if match:
                return int(match.group(1))
    return None


def _is_dashboard_redirect(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.rstrip("/") or "/"
    return path in ("/", "/dashboard", "/ceo")


def _edit_form_from_get(html: str, booking_id: int, status: str) -> dict:
    form: dict = {"action": "save", "status": status}

    for match in re.finditer(
        r'<input[^>]+name="([^"]+)"[^>]*value="([^"]*)"',
        html,
    ):
        name, value = match.group(1), match.group(2)
        if name in ("secret_key", "webhook_secret", "action", "status"):
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
        if match.group(1) != "status":
            form[match.group(1)] = match.group(2)

    if 'name="gst_enabled"' in html and re.search(
        r'name="gst_enabled"[^>]*checked', html
    ):
        form["gst_enabled"] = "on"

    form["action"] = "save"
    form["status"] = status
    form["double_booking_override_confirm"] = "on"
    return form


def main() -> int:
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
        print("FAIL: Set PRODUCTION_TEST_USERNAME and PRODUCTION_TEST_PASSWORD (or STAFF_*).")
        return 1

    client = ProductionClient()
    results: dict = {"steps": [], "production_url": PRODUCTION_URL}

    if not _login(client, username, password):
        print("FAIL: Could not log in to production.")
        return 1
    results["steps"].append("Logged in")

    customer_name = "{0} {1}".format(TEST_MARKER, int(time()))
    move_date = (date.today() + timedelta(days=60)).isoformat()
    create_form = _new_booking_form(client, customer_name, move_date)

    status, html, final_url, headers = client.request(
        "POST", "/bookings/new", create_form
    )
    booking_id = _extract_booking_id_from_html(html)
    if not booking_id:
        booking_id = _extract_booking_id_from_redirect(headers.get("Location", final_url))
    if not booking_id:
        booking_id = _find_test_booking_id(client, customer_name)
    results["create"] = {
        "status": status,
        "booking_id": booking_id,
        "customer_name": customer_name,
    }
    if status >= 400 or not booking_id:
        results["passed"] = False
        out = RESULTS_DIR / "cancel_test_results.json"
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(results, indent=2))
        print("FAIL: Could not create test booking (status {0}).".format(status))
        return 1
    results["steps"].append("Created test booking #{0}".format(booking_id))

    get_status, edit_html, _, _ = client.request(
        "GET", "/bookings/{0}/edit".format(booking_id)
    )
    cancel_form = _edit_form_from_get(edit_html, booking_id, "Cancelled")
    cancel_form["notes"] = TEST_MARKER + " cancelled"

    cancel_status, cancel_html, cancel_url, cancel_headers = client.request(
        "POST",
        "/bookings/{0}/edit".format(booking_id),
        cancel_form,
    )
    location = cancel_headers.get("Location", cancel_url)
    cancel_ok = (
        cancel_status in (200, 302)
        and cancel_status != 500
        and "Internal Server Error" not in cancel_html[:300]
        and _is_dashboard_redirect(cancel_url)
    )
    results["cancel_post"] = {
        "status": cancel_status,
        "location": location,
        "final_url": cancel_url,
        "is_500": cancel_status == 500 or "Internal Server Error" in cancel_html[:200],
        "redirected_to_dashboard": _is_dashboard_redirect(cancel_url),
    }
    if not cancel_ok:
        results["passed"] = False
        out = RESULTS_DIR / "cancel_test_results.json"
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(results, indent=2))
        print("FAIL: Cancel did not succeed (status {0}, url {1}).".format(
            cancel_status, cancel_url
        ))
        return 1
    results["steps"].append("Cancel POST returned {0}".format(cancel_status))

    view_status, view_html, _, _ = client.request(
        "GET", "/bookings/{0}".format(booking_id)
    )
    status_ok = "Cancelled" in view_html and customer_name in view_html
    results["view_booking"] = {"status": view_status, "shows_cancelled": status_ok}

    dash_status, dash_html, _, _ = client.request(
        "GET", "/dashboard?filter=cancelled"
    )
    in_cancelled_filter = customer_name in dash_html
    results["cancelled_filter"] = {
        "status": dash_status,
        "found": in_cancelled_filter,
    }

    search_status, search_html, _, _ = client.request(
        "GET", "/bookings/search?q=" + urllib.parse.quote(TEST_MARKER)
    )
    still_exists = customer_name in search_html
    results["still_in_database"] = {
        "status": search_status,
        "found": still_exists,
        "via": "search",
    }

    passed = (
        cancel_ok
        and view_status == 200
        and status_ok
        and still_exists
        and in_cancelled_filter
    )
    results["passed"] = passed

    out = RESULTS_DIR / "cancel_test_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    print("RESULTS_FILE", out)
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
