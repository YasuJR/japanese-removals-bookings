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

TEST_MARKER = "PROD-CANCEL-TEST"


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
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=120)
            html = resp.read().decode("utf-8", errors="replace")
            return resp.status, html, resp.geturl(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            html = exc.read().decode("utf-8", errors="replace")
            if follow and exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location") or ""
                if location.startswith("/"):
                    location = PRODUCTION_URL + location
                if location:
                    return self.request("GET", location.replace(PRODUCTION_URL, ""), None)
            return exc.code, html, url, dict(exc.headers)


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
        "start_time": "09:00",
        "duration_hours": "2",
        "num_movers": "2",
        "notes": TEST_MARKER + " — safe to leave cancelled",
        "hourly_rate": "1",
        "callout_fee": "0",
        "gst_enabled": "on",
        "payment_status": "Unpaid",
        "invoice_status": "",
        "truck_assigned": "Truck 1",
        "crew": "Ken",
    }


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
    status, html, _, _ = client.request("GET", "/bookings/all")
    if status != 200:
        return None
    pattern = r'href="/bookings/(\d+)/edit"[^>]*>[^<]*' + re.escape(customer_name)
    match = re.search(pattern, html)
    if match:
        return int(match.group(1))
    pattern = r"/bookings/(\d+)/edit.*?{0}".format(re.escape(customer_name))
    match = re.search(pattern, html, re.DOTALL)
    if match:
        return int(match.group(1))
    return None


def _edit_form_from_get(html: str, booking_id: int, status: str) -> dict:
    def field(name: str, default: str = "") -> str:
        match = re.search(
            r'name="{0}"[^>]*(?:value="([^"]*)")?'.format(re.escape(name)),
            html,
        )
        if not match:
            return default
        return match.group(1) if match.lastindex else default

    form = {
        "action": "save",
        "customer_name": field("customer_name"),
        "phone": field("phone"),
        "email": field("email"),
        "pickup_address": field("pickup_address"),
        "delivery_address": field("delivery_address"),
        "move_date": field("move_date"),
        "status": status,
        "start_time": field("start_time", "09:00"),
        "duration_hours": field("duration_hours", "2"),
        "num_movers": field("num_movers", "2"),
        "notes": field("notes"),
        "hourly_rate": field("hourly_rate", "1"),
        "callout_fee": field("callout_fee", "0"),
        "payment_status": field("payment_status", "Unpaid"),
        "invoice_status": field("invoice_status", ""),
        "truck_assigned": field("truck_assigned", ""),
    }
    if 'name="gst_enabled"' in html and 'name="gst_enabled" checked' not in html:
        if re.search(r'name="gst_enabled"[^>]*checked', html):
            form["gst_enabled"] = "on"
    else:
        form["gst_enabled"] = "on"
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
    results["cancel_post"] = {
        "status": cancel_status,
        "location": location,
        "is_500": cancel_status == 500 or "Internal Server Error" in cancel_html[:200],
    }
    if cancel_status == 500 or "Internal Server Error" in cancel_html[:300]:
        results["passed"] = False
        out = RESULTS_DIR / "cancel_test_results.json"
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(results, indent=2))
        print("FAIL: Cancel returned 500.")
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

    still_exists_status, all_html, _, _ = client.request("GET", "/bookings/all")
    still_exists = customer_name in all_html
    results["still_in_database"] = {
        "status": still_exists_status,
        "found": still_exists,
    }

    passed = (
        cancel_status in (200, 302)
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
