#!/usr/bin/env python3
"""Production smoke test — Staff Portal Month/Week calendar in browser HTML."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "test_results" / "production"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

PRODUCTION_URL = os.environ.get(
    "APP_BASE_URL", "https://japanese-removals-bookings.onrender.com"
).rstrip("/")

def _yasu_staff_id(home_html: str) -> str | None:
    match = re.search(
        r'href="[^"]*staff_id=(\d+)[^"]*"[^>]*>\s*Yasu\s*</a>',
        home_html,
        re.I,
    )
    return match.group(1) if match else None


def _checks(yasu_id: str | None) -> list[tuple[str, str, tuple[str, ...]]]:
    base = [
        (
            "all_month",
            "/staff?range=calendar&staff_id=all&cal_view=month",
            ("staff-cal-view-tabs", "staff-cal-month-grid", "calendar-weekdays", "MONTH"),
        ),
        (
            "all_week",
            "/staff?range=calendar&staff_id=all&cal_view=week",
            ("staff-cal-week-board", "Previous Week", "staff-cal-week-col-label"),
        ),
    ]
    if yasu_id:
        base.extend(
            [
                (
                    "yasu_month",
                    "/staff?range=calendar&staff_id={0}&cal_view=month".format(yasu_id),
                    ("staff-cal-month-grid", "calendar-weekdays"),
                ),
                (
                    "yasu_week",
                    "/staff?range=calendar&staff_id={0}&cal_view=week".format(yasu_id),
                    ("staff-cal-week-board", "MON", "SUN"),
                ),
            ]
        )
    return base


def _fetch(path: str) -> str:
    req = urllib.request.Request(
        PRODUCTION_URL + path,
        headers={"User-Agent": "staff-calendar-verify/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> int:
    home = _fetch("/staff?range=today&staff_id=all")
    yasu_id = _yasu_staff_id(home)
    results = []
    all_ok = True
    if not yasu_id:
        all_ok = False
        results.append(
            {
                "check": "yasu_staff_id",
                "pass": False,
                "missing": ["Could not find Yasu staff_id on portal"],
            }
        )
    for key, path, needles in _checks(yasu_id):
        try:
            html = _fetch(path)
            missing = [n for n in needles if n not in html]
            ok = not missing
        except OSError as exc:
            ok = False
            missing = [str(exc)]
            html = ""
        results.append(
            {
                "check": key,
                "url": PRODUCTION_URL + path,
                "pass": ok,
                "missing": missing,
            }
        )
        if not ok:
            all_ok = False
    out = {"checks": results, "pass": all_ok}
    out_path = RESULTS_DIR / "staff_calendar_verify_results.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
