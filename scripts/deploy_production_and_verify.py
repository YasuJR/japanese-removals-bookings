#!/usr/bin/env python3
"""Trigger Render deploy (optional) and poll /health until git SHA matches GitHub main."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_URL = os.environ.get(
    "APP_BASE_URL", "https://japanese-removals-bookings.onrender.com"
).rstrip("/")
TARGET_SHA = os.environ.get("DEPLOY_TARGET_SHA", "").strip()


def _main_sha() -> str:
    out = subprocess.check_output(
        ["git", "rev-parse", "origin/main"],
        cwd=ROOT,
        text=True,
    )
    return out.strip()


def _health() -> dict:
    req = urllib.request.Request(
        PRODUCTION_URL + "/health",
        headers={"User-Agent": "deploy-production-verify/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    expected = TARGET_SHA or _main_sha()
    expected_prefix = expected[:12]

    if os.environ.get("TRIGGER_RENDER_DEPLOY", "").strip() == "1":
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "render_link_database.py"),
            "--deploy",
            "--clear-cache",
        ]
        proc = subprocess.run(cmd, cwd=ROOT)
        if proc.returncode != 0:
            return proc.returncode

    print("Waiting for production git_commit to match {0}...".format(expected_prefix))
    deadline = time.time() + float(os.environ.get("DEPLOY_WAIT_SECONDS", "900"))
    last = {}
    while time.time() < deadline:
        try:
            last = _health()
        except OSError as exc:
            print("health error:", exc)
            time.sleep(15)
            continue
        live = str(last.get("git_commit_full") or last.get("git_commit") or "")
        print(
            "  live={0} table={1} rows={2}".format(
                live[:12],
                last.get("booking_crew_hours_table"),
                last.get("booking_crew_hours_rows"),
            )
        )
        if live.startswith(expected_prefix) or expected.startswith(live[:12]):
            print("PASS: production matches main")
            print(json.dumps(last, indent=2))
            return 0
        time.sleep(20)

    print("TIMEOUT: production still not on {0}".format(expected_prefix))
    print(json.dumps(last, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
