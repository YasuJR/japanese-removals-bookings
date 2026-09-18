#!/usr/bin/env python3
"""Production environment diagnosis — deploy SHA, GitHub main, optional Postgres checks."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "test_results" / "production"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

PRODUCTION_URL = os.environ.get(
    "APP_BASE_URL", "https://japanese-removals-bookings.onrender.com"
).rstrip("/")
SERVICE_NAME = "japanese-removals-bookings"
GITHUB_MAIN = "https://api.github.com/repos/YasuJR/japanese-removals-bookings/commits/main"


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "production-environment-diagnose/1.0"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _git_main_sha() -> str:
    try:
        data = _fetch_json(GITHUB_MAIN)
        return str(data.get("sha") or "")
    except OSError:
        out = subprocess.check_output(
            ["git", "rev-parse", "origin/main"],
            cwd=ROOT,
            text=True,
        )
        return out.strip()


def _load_render_api_key() -> str:
    key = (os.environ.get("RENDER_API_KEY") or "").strip()
    if key:
        return key
    cli = Path.home() / ".render" / "cli.yaml"
    if cli.is_file():
        for line in cli.read_text(encoding="utf-8").splitlines():
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
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _unwrap(items: list) -> list:
    out = []
    for item in items or []:
        if isinstance(item, dict):
            for key in ("service", "deploy", "envVar"):
                if key in item and isinstance(item[key], dict):
                    out.append(item[key])
                    break
            else:
                out.append(item)
    return out


def _postgres_checks(database_url: str) -> dict:
    import psycopg2

    out: dict = {}
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                  SELECT FROM information_schema.tables
                  WHERE table_schema = 'public'
                    AND table_name = 'booking_crew_hours'
                )
                """
            )
            out["booking_crew_hours_table_exists"] = bool(cur.fetchone()[0])
            if out["booking_crew_hours_table_exists"]:
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'booking_crew_hours'
                    ORDER BY ordinal_position
                    """
                )
                out["booking_crew_hours_columns"] = [r[0] for r in cur.fetchall()]
                cur.execute("SELECT COUNT(*) FROM booking_crew_hours")
                out["booking_crew_hours_row_count"] = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bookings'
                  AND column_name IN (
                    'start_time','finish_time',
                    'actual_start_time','actual_finish_time','actual_duration'
                  )
                ORDER BY column_name
                """
            )
            out["bookings_time_columns"] = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    return out


def _sample_crew_times(database_url: str, booking_id: int) -> dict:
    import psycopg2

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, start_time, finish_time,
                       actual_start_time, actual_finish_time
                FROM bookings WHERE id = %s
                """,
                (booking_id,),
            )
            row = cur.fetchone()
            booking = (
                {
                    "id": row[0],
                    "start_time": row[1],
                    "finish_time": row[2],
                    "actual_start_time": row[3],
                    "actual_finish_time": row[4],
                }
                if row
                else None
            )
            crew_rows = []
            if booking:
                cur.execute(
                    """
                    SELECT crew_id, start_time, finish_time,
                           actual_start_time, actual_finish_time
                    FROM booking_crew_hours
                    WHERE booking_id = %s
                    ORDER BY crew_id
                    """,
                    (booking_id,),
                )
                for r in cur.fetchall():
                    crew_rows.append(
                        {
                            "crew_id": r[0],
                            "start_time": r[1],
                            "finish_time": r[2],
                            "actual_start_time": r[3],
                            "actual_finish_time": r[4],
                        }
                    )
            cur.execute(
                "SELECT id, name FROM crew_members WHERE name IN ('Yasu','Will','Katsu')"
            )
            crew_ids = {r[1]: r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    return {
        "booking": booking,
        "crew_overrides": crew_rows,
        "crew_ids": crew_ids,
    }


def main() -> int:
    health = _fetch_json(PRODUCTION_URL + "/health")
    prod_prefix = str(health.get("git_commit") or "")
    main_sha = _git_main_sha()
    main_prefix = main_sha[:12]

    report: dict = {
        "production_url": PRODUCTION_URL,
        "health": health,
        "production_git_commit_prefix": prod_prefix,
        "github_main_sha": main_sha,
        "github_main_prefix": main_prefix,
        "shas_match": prod_prefix == main_prefix or (
            main_sha.startswith(prod_prefix) if prod_prefix else False
        ),
        "commits_on_main_not_in_production": [],
        "render_api": {"ok": False, "deploys": []},
        "database": {"checked": False},
    }

    try:
        out = subprocess.check_output(
            ["git", "log", "--oneline", "{0}..{1}".format(prod_prefix, main_sha)],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        report["commits_on_main_not_in_production"] = [
            line for line in out.splitlines() if line.strip()
        ]
    except subprocess.CalledProcessError:
        pass

    api_key = _load_render_api_key()
    if api_key:
        try:
            services = _unwrap(_render_api("/services?limit=100", api_key))
            svc = next((s for s in services if s.get("name") == SERVICE_NAME), None)
            if svc:
                deploys = _unwrap(
                    _render_api("/services/{0}/deploys?limit=8".format(svc["id"]), api_key)
                )
                for d in deploys:
                    c = d.get("commit") or {}
                    report["render_api"]["deploys"].append(
                        {
                            "status": d.get("status"),
                            "createdAt": d.get("createdAt"),
                            "finishedAt": d.get("finishedAt"),
                            "commit_id": c.get("id"),
                            "message": (c.get("message") or "")[:80],
                        }
                    )
                report["render_api"]["ok"] = True
        except urllib.error.HTTPError as exc:
            report["render_api"]["error"] = "HTTP {0}".format(exc.code)

    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if db_url.startswith("postgres"):
        try:
            report["database"] = {"checked": True, **_postgres_checks(db_url)}
            sample_id = os.environ.get("DIAG_BOOKING_ID")
            if sample_id:
                report["sample_booking"] = _sample_crew_times(db_url, int(sample_id))
        except Exception as exc:
            report["database"] = {"checked": True, "error": str(exc)}

    out_path = RESULTS_DIR / "environment_diagnosis.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["shas_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
