#!/usr/bin/env python3
"""Production verification for Staff Portal Star Points owner management UI."""

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
STAFF_TARGET = (os.environ.get("STAR_POINTS_VERIFY_STAFF") or "Will").strip()


def _load_credentials() -> tuple[str, str]:
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
        env_path = ROOT / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key == "STAFF_USERNAME" and not username:
                    username = value
                elif key == "STAFF_PASSWORD" and not password:
                    password = value
    if not username or not password:
        raise SystemExit(
            "Set STAFF_USERNAME/STAFF_PASSWORD or PRODUCTION_TEST_USERNAME/PASSWORD."
        )
    return username, password


class Client:
    def __init__(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def get(self, path: str) -> tuple[int, str]:
        req = urllib.request.Request(PRODUCTION_URL + path)
        try:
            with self.opener.open(req, timeout=90) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def post(
        self, path: str, data: dict[str, str], *, follow: bool = True
    ) -> tuple[int, str, str | None]:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            PRODUCTION_URL + path,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self.opener.open(req, timeout=90) as resp:
                location = resp.headers.get("Location")
                html = resp.read().decode("utf-8", errors="replace")
                if follow and location:
                    status, html = self.get(location)
                    return status, html, location
                return resp.status, html, location
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location")
            html = exc.read().decode("utf-8", errors="replace")
            if follow and location:
                status, html = self.get(location)
                return status, html, location
            return exc.code, html, location


def _crew_id_for_staff(html: str, staff_name: str) -> int | None:
    pattern = r'href="/staff\?staff_id=(\d+)[^"]*">\s*{0}\s*</a>'.format(
        re.escape(staff_name)
    )
    match = re.search(pattern, html, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _points_from_html(html: str) -> int | None:
    match = re.search(r"(\d+)\s*/\s*10", html)
    return int(match.group(1)) if match else None


def main() -> int:
    username, password = _load_credentials()
    client = Client()
    results: list[dict] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append({"name": name, "pass": ok, "detail": detail})
        status = "PASS" if ok else "FAIL"
        print("{0}: {1} {2}".format(status, name, detail))

    login_status, _, _ = client.post(
        "/login",
        {"username": username, "password": password},
        follow=True,
    )
    record(
        "Office owner login",
        login_status == 200,
        "status={0}".format(login_status),
    )

    portal_status, portal_html = client.get("/staff?range=week")
    record("Staff portal loads", portal_status == 200)
    crew_id = _crew_id_for_staff(portal_html, STAFF_TARGET)
    record(
        "Resolve crew id for {0}".format(STAFF_TARGET),
        crew_id is not None,
        "crew_id={0}".format(crew_id),
    )
    if crew_id is None:
        _write_results(results)
        return 1

    owner_status, owner_html = client.get(
        "/staff?staff_id={0}&range=week".format(crew_id)
    )
    record("Owner staff view loads", owner_status == 200)
    record(
        "Edit name visible for owner",
        "Edit name for {0}".format(STAFF_TARGET) in owner_html,
    )
    record(
        "Star Points management visible for owner",
        "STAR POINTS MANAGEMENT" in owner_html,
    )

    start_points = _points_from_html(owner_html)
    record(
        "Read current star points",
        start_points is not None,
        "points={0}".format(start_points),
    )

    if start_points != 0:
        reset_status, _, _ = client.post(
            "/staff/crew/{0}/star-points/reset".format(crew_id),
            {
                "confirm": "1",
                "range": "week",
                "week": "0",
                "staff_id": str(crew_id),
            },
            follow=True,
        )
        record("Reset to zero before test", reset_status == 200)

    inc_status, inc_html, _ = client.post(
        "/staff/crew/{0}/star-points/adjust".format(crew_id),
        {
            "action": "increment",
            "range": "week",
            "week": "0",
            "staff_id": str(crew_id),
        },
        follow=True,
    )
    after_inc = _points_from_html(inc_html)
    record(
        "Increment 0 to 1",
        inc_status == 200 and after_inc == 1,
        "points={0}".format(after_inc),
    )

    reload_status, reload_html = client.get(
        "/staff?staff_id={0}&range=week".format(crew_id)
    )
    reload_points = _points_from_html(reload_html)
    record(
        "Persist after reload",
        reload_status == 200 and reload_points == 1,
        "points={0}".format(reload_points),
    )

    dec_status, dec_html, _ = client.post(
        "/staff/crew/{0}/star-points/adjust".format(crew_id),
        {
            "action": "decrement",
            "range": "week",
            "week": "0",
            "staff_id": str(crew_id),
        },
        follow=True,
    )
    after_dec = _points_from_html(dec_html)
    record(
        "Decrement 1 to 0",
        dec_status == 200 and after_dec == 0,
        "points={0}".format(after_dec),
    )

    other_id = None
    for match in re.finditer(r'href="/staff\?staff_id=(\d+)[^"]*">([^<]+)</a>', portal_html):
        cid = int(match.group(1))
        name = match.group(2).strip()
        if cid != crew_id:
            other_id = cid
            other_name = name
            break
    if other_id is not None:
        _, other_html = client.get("/staff?staff_id={0}&range=week".format(other_id))
        other_points = _points_from_html(other_html)
        record(
            "Other staff unaffected",
            other_points != 1,
            "{0} points={1}".format(other_name, other_points),
        )
    else:
        record("Other staff unaffected", True, "skipped — single staff tab")

    viewer = Client()
    viewer_status, viewer_html = viewer.get(
        "/staff?staff_id={0}&range=week".format(crew_id)
    )
    record("Anonymous viewer loads staff page", viewer_status == 200)
    record(
        "Anonymous viewer sees display only",
        "STAR POINTS" in viewer_html
        and "STAR POINTS MANAGEMENT" not in viewer_html
        and "Edit name for" not in viewer_html,
    )

    _write_results(results)
    failed = [item for item in results if not item["pass"]]
    return 1 if failed else 0


def _write_results(results: list[dict]) -> None:
    out = ROOT / "test_results" / "production" / "star_points_verify_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": PRODUCTION_URL,
        "staff": STAFF_TARGET,
        "results": results,
        "pass": all(item["pass"] for item in results),
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("Wrote {0}".format(out))


if __name__ == "__main__":
    raise SystemExit(main())
