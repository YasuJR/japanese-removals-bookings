#!/usr/bin/env python3
"""Benchmark Staff Portal routes: response time and DB query counts."""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "benchmark-staff-portal")

import auth
import database as db
import db_backend
from app import app

_ORIGINAL_EXECUTE = db_backend.CompatConnection.execute
_QUERY_LOG: List[Tuple[str, float]] = []


def _instrumented_execute(self, sql, params=()):
    started = time.perf_counter()
    try:
        return _ORIGINAL_EXECUTE(self, sql, params)
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        text = " ".join(str(sql or "").split())[:160]
        _QUERY_LOG.append((text, elapsed_ms))


def _crew_id(name: str) -> str:
    db.init_db()
    for row in db.list_crew_members(active_only=False):
        if row.get("name") == name:
            return str(row["id"])
    return "1"


def _bench_path(path: str, repeats: int = 5) -> Dict[str, Any]:
    global _QUERY_LOG
    client = app.test_client()
    times_ms: List[float] = []
    query_counts: List[int] = []
    for _ in range(repeats):
        _QUERY_LOG = []
        started = time.perf_counter()
        response = client.get(path)
        elapsed_ms = (time.perf_counter() - started) * 1000
        times_ms.append(elapsed_ms)
        query_counts.append(len(_QUERY_LOG))
        status = response.status_code
        size = len(response.get_data())
    times_ms.sort()
    query_counts.sort()
    mid = len(times_ms) // 2
    return {
        "path": path,
        "status": status,
        "bytes": size,
        "ms_median": round(times_ms[mid], 2),
        "ms_min": round(times_ms[0], 2),
        "ms_max": round(times_ms[-1], 2),
        "db_queries_median": query_counts[mid],
        "db_queries_min": query_counts[0],
        "db_queries_max": query_counts[-1],
        "db_ms_total_last": round(sum(ms for _, ms in _QUERY_LOG), 2),
        "sample_queries": list({q for q, _ in _QUERY_LOG})[:8],
    }


def main() -> int:
    db_backend.CompatConnection.execute = _instrumented_execute
    db.init_db()
    yasu_id = _crew_id("Yasu")
    paths = [
        "/staff?range=today&staff_id={0}".format(yasu_id),
        "/staff?range=week&staff_id={0}".format(yasu_id),
        "/staff?range=history&staff_id={0}".format(yasu_id),
        "/staff?range=calendar&staff_id={0}&year=2026&month=9".format(yasu_id),
        "/staff?range=week&staff_id=all",
        "/staff?range=today&staff_id=all",
    ]
    results = [_bench_path(path) for path in paths]
    payload = {"results": results}
    out = ROOT / "test_results" / "performance" / "staff_portal_benchmark.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("Wrote {0}".format(out))
    for row in results:
        print(
            "{0}  {1}ms  {2} queries  {3} bytes".format(
                row["path"],
                row["ms_median"],
                row["db_queries_median"],
                row["bytes"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
