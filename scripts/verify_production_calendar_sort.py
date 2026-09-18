#!/usr/bin/env python3
"""Production check — calendar jobs on a day appear in true start-time order."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from booking_times import job_start_minutes, time_value_to_minutes

PRODUCTION_URL = os.environ.get(
    "APP_BASE_URL", "https://japanese-removals-bookings.onrender.com"
).rstrip("/")
VERIFY_DAY = os.environ.get("CALENDAR_SORT_VERIFY_DAY", "2026-09-19")
RESULTS_DIR = ROOT / "test_results" / "production"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _fetch_staff_calendar_day(day_iso: str) -> str:
    query = (
        "/staff?range=calendar&staff_id=all&year={y}&month={m}&day={d}"
        "&cal_view=month"
    ).format(
        y=day_iso[:4],
        m=int(day_iso[5:7]),
        d=day_iso,
    )
    req = urllib.request.Request(
        PRODUCTION_URL + query,
        headers={"User-Agent": "calendar-sort-verify/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _jobs_from_day_panel(html: str) -> list[dict]:
    """Parse selected-day job cards from staff calendar HTML."""
    marker = "staff-cal-selected-day"
    idx = html.find(marker)
    panel = html[idx : idx + 200_000] if idx >= 0 else html
    jobs: list[dict] = []
    for block in re.findall(
        r'<summary class="staff-cal-job-summary[^"]*">(.*?)</summary>',
        panel,
        re.S | re.I,
    ):
        time_m = re.search(r'class="cal-booking-time"[^>]*>([^<]+)', block)
        name_m = re.search(r'class="cal-booking-customer"[^>]*>([^<]+)', block)
        if not time_m or not name_m:
            continue
        time_text = time_m.group(1).strip()
        start_part = re.split(r"[–\-—]", time_text, maxsplit=1)[0].strip()
        minutes = time_value_to_minutes(start_part)
        if minutes is None and "TBC" in time_text.upper():
            minutes = 24 * 60
        jobs.append(
            {
                "id": len(jobs) + 1,
                "customer_name": name_m.group(1).strip(),
                "start_display": start_part,
                "start_minutes": minutes if minutes is not None else 24 * 60,
            }
        )
    return jobs


def _is_sorted(jobs: list[dict]) -> bool:
    keys = [
        (
            job_start_minutes(
                {
                    "id": job.get("id"),
                    "start_minutes": job.get("start_minutes"),
                    "has_start_time": job.get("start_minutes", 24 * 60) < 24 * 60,
                    "time_range": job.get("start_display"),
                }
            ),
            int(job.get("id") or 0),
        )
        for job in jobs
    ]
    return keys == sorted(keys)


def main() -> int:
    html = _fetch_staff_calendar_day(VERIFY_DAY)
    jobs = _jobs_from_day_panel(html)
    odette_idx = next(
        (i for i, j in enumerate(jobs) if "Odette" in j.get("customer_name", "")),
        None,
    )
    joshua_idx = next(
        (i for i, j in enumerate(jobs) if "Joshua" in j.get("customer_name", "")),
        None,
    )
    pair_ok = (
        odette_idx is not None
        and joshua_idx is not None
        and odette_idx < joshua_idx
    )
    sorted_ok = _is_sorted(jobs)
    result = {
        "day": VERIFY_DAY,
        "job_count": len(jobs),
        "jobs": [
            {
                "customer": j["customer_name"],
                "start": j["start_display"],
                "minutes": j["start_minutes"],
            }
            for j in jobs
        ],
        "odette_before_joshua": pair_ok,
        "monotonic_start_minutes": sorted_ok,
        "pass": sorted_ok and (pair_ok or odette_idx is None or joshua_idx is None),
    }
    out_path = RESULTS_DIR / "calendar_sort_verify_results.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["pass"]:
        print("FAIL: calendar sort verification")
        return 1
    print("PASS: calendar sort verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
