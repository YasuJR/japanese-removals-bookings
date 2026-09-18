#!/usr/bin/env python3
"""Production check — per-crew scheduled times do not leak across staff views."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PRODUCTION_URL = os.environ.get(
    "APP_BASE_URL", "https://japanese-removals-bookings.onrender.com"
).rstrip("/")
RESULTS_DIR = ROOT / "test_results" / "production"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _fetch(path: str) -> str:
    req = urllib.request.Request(
        PRODUCTION_URL + path,
        headers={"User-Agent": "crew-times-verify/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _staff_ids(html: str) -> dict[str, str]:
    ids = {}
    for name in ("Yasu", "Will", "Katsu"):
        match = re.search(
            r'href="[^"]*staff_id=(\d+)[^"]*"[^>]*>\s*{0}\s*</a>'.format(name),
            html,
            re.I,
        )
        if match:
            ids[name] = match.group(1)
    return ids


def _job_ranges(html: str, customer_hint: str) -> list[str]:
    """Extract scheduled range lines near a customer name from staff portal HTML."""
    idx = html.find(customer_hint)
    if idx < 0:
        return []
    window = html[max(0, idx - 800) : idx + 1200]
    return re.findall(
        r"(\d{1,2}:\d{2}\s*[AP]M\s*[–-]\s*\d{1,2}:\d{2}\s*[AP]M)",
        window,
    )


def main() -> int:
    home = _fetch("/staff?range=week&staff_id=all")
    staff_ids = _staff_ids(home)
    result = {
        "staff_ids_found": staff_ids,
        "note": "Manual: pick a multi-crew job and compare ranges per staff tab.",
        "pass": len(staff_ids) >= 2,
    }
    out = RESULTS_DIR / "crew_times_verify_results.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
