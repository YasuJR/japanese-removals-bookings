#!/usr/bin/env python3
"""Measure page-load performance: response time, DB queries, external API calls."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "benchmark-secret-key")

import auth
import database as db
import db_backend
from app import app

# ---------------------------------------------------------------------------
# Instrumentation
# ---------------------------------------------------------------------------

_ORIGINAL_EXECUTE = db_backend.CompatConnection.execute
_EXTERNAL_CALLS: Counter = Counter()
_QUERY_LOG: List[Tuple[str, float]] = []
_SLOW_QUERY_MS = 25.0


def _normalize_sql(sql: str) -> str:
    text = " ".join((sql or "").split())
    return text[:180]


def _instrumented_execute(self, sql, params=()):
    started = time.perf_counter()
    try:
        return _ORIGINAL_EXECUTE(self, sql, params)
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        _QUERY_LOG.append((_normalize_sql(str(sql)), elapsed_ms))


def _patch_external(module_name: str, attr: str, label: str) -> None:
    import importlib

    mod = importlib.import_module(module_name)
    original = getattr(mod, attr)

    def wrapper(*args, **kwargs):
        _EXTERNAL_CALLS[label] += 1
        return original(*args, **kwargs)

    setattr(mod, attr, wrapper)


_ORIGINAL_OPEN_CONNECTION = db_backend._open_connection
_CONNECTION_OPENS = 0


def _instrumented_open_connection():
    global _CONNECTION_OPENS
    _CONNECTION_OPENS += 1
    return _ORIGINAL_OPEN_CONNECTION()


def install_instrumentation() -> None:
    db_backend.CompatConnection.execute = _instrumented_execute
    db_backend._open_connection = _instrumented_open_connection
    patches = [
        ("integrations.google_calendar", "sync_booking_event", "google_calendar.sync"),
        ("integrations.google_calendar", "delete_booking_event", "google_calendar.delete"),
        ("integrations.xero", "fetch_invoice", "xero.fetch_invoice"),
        ("integrations.xero", "resolve_invoice_status", "xero.resolve_invoice_status"),
        ("integrations.xero", "create_invoice_for_booking", "xero.create_invoice"),
        ("integrations.stripe", "create_checkout_session", "stripe.checkout"),
        ("integrations.stripe", "retrieve_checkout_session", "stripe.retrieve_checkout"),
    ]
    for module_name, attr, label in patches:
        try:
            _patch_external(module_name, attr, label)
        except AttributeError:
            pass


def reset_metrics() -> None:
    global _CONNECTION_OPENS
    _QUERY_LOG.clear()
    _EXTERNAL_CALLS.clear()
    _CONNECTION_OPENS = 0


@dataclass
class PageBenchmark:
    name: str
    path: str
    status_code: int = 0
    total_ms: float = 0.0
    query_count: int = 0
    db_ms: float = 0.0
    connection_opens: int = 0
    slow_queries: List[Tuple[str, float]] = field(default_factory=list)
    repeated_queries: List[Tuple[str, int]] = field(default_factory=list)
    external_calls: Dict[str, int] = field(default_factory=dict)
    html_bytes: int = 0
    error: str = ""
    query_log: List[Tuple[str, float]] = field(default_factory=list)


def _analyze_queries() -> Tuple[List[Tuple[str, float]], List[Tuple[str, int]]]:
    slow = [(sql, ms) for sql, ms in _QUERY_LOG if ms >= _SLOW_QUERY_MS]
    slow.sort(key=lambda item: item[1], reverse=True)
    counts = Counter(sql for sql, _ in _QUERY_LOG)
    repeated = [(sql, count) for sql, count in counts.items() if count > 1]
    repeated.sort(key=lambda item: item[1], reverse=True)
    return slow[:10], repeated[:10]


def _count_connection_opens() -> int:
    return _CONNECTION_OPENS


def _top_bottleneck(result: PageBenchmark) -> str:
    parts = []
    if result.query_count >= 50:
        parts.append("{0} DB queries".format(result.query_count))
    init_count = sum(
        1
        for sql, _ in result.query_log
        if "CREATE TABLE" in sql or "pg_advisory" in sql or "PRAGMA table_info" in sql
    )
    if init_count:
        parts.append("schema init/migration queries: {0}".format(init_count))
    if result.connection_opens >= 20:
        parts.append("{0} connection opens".format(result.connection_opens))
    if result.repeated_queries:
        sql, count = result.repeated_queries[0]
        parts.append('repeat query x{0}: "{1}…"'.format(count, sql[:60]))
    if result.external_calls:
        parts.append(
            "external APIs: "
            + ", ".join("{0}×{1}".format(k, v) for k, v in result.external_calls.items())
        )
    if result.slow_queries:
        sql, ms = result.slow_queries[0]
        parts.append("slowest query {0:.0f}ms: {1}…".format(ms, sql[:50]))
    if not parts:
        parts.append(
            "general Python/template work ({0:.0f}ms non-DB)".format(
                max(0, result.total_ms - result.db_ms)
            )
        )
    return "; ".join(parts)


def benchmark_get(client, name: str, path: str) -> PageBenchmark:
    reset_metrics()
    result = PageBenchmark(name=name, path=path)
    started = time.perf_counter()
    try:
        response = client.get(path)
        result.status_code = response.status_code
        body = response.get_data()
        result.html_bytes = len(body)
        if response.status_code != 200:
            result.error = body[:300].decode("utf-8", errors="replace")
    except Exception as exc:
        result.error = str(exc)
    result.total_ms = (time.perf_counter() - started) * 1000
    result.query_log = list(_QUERY_LOG)
    result.query_count = len(result.query_log)
    result.db_ms = sum(ms for _, ms in result.query_log)
    result.connection_opens = _count_connection_opens()
    result.slow_queries, result.repeated_queries = _analyze_queries()
    result.external_calls = dict(_EXTERNAL_CALLS)
    return result


def _setup_client() -> Tuple[Any, int]:
    db.init_db()
    username = "perf-bench-{0}".format(os.getpid())
    password = "perf-bench-pass"
    uid = db.create_staff_user(username, auth.hash_password(password), "Perf Bench")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = username

    row = db.list_all()
    booking_id = int(row[0]["id"]) if row else 0
    return client, booking_id


def run_benchmarks(label: str) -> List[PageBenchmark]:
    install_instrumentation()
    client, booking_id = _setup_client()
    if not booking_id:
        raise SystemExit("No bookings in database — add sample data first.")

    pages = [
        ("Home", "/"),
        ("Dashboard", "/dashboard"),
        ("Calendar", "/calendar"),
        ("New Booking", "/bookings/new"),
        ("Crew", "/crew-schedule"),
        ("Driver", "/driver"),
        ("Invoices", "/invoices"),
        ("Search", "/bookings/search?q=test"),
        ("Booking Details", "/bookings/{0}".format(booking_id)),
        ("Edit Booking", "/bookings/{0}/edit".format(booking_id)),
    ]

    results = []
    print("\n=== {0} ===".format(label))
    print(
        "{:<18} {:>8} {:>8} {:>8} {:>10} {:>8}".format(
            "Page", "Total", "DB ms", "Queries", "HTML KB", "API"
        )
    )
    print("-" * 72)
    for name, path in pages:
        result = benchmark_get(client, name, path)
        results.append(result)
        api_total = sum(result.external_calls.values())
        print(
            "{:<18} {:>7.0f}ms {:>7.0f}ms {:>8} {:>10.1f} {:>8}".format(
                name,
                result.total_ms,
                result.db_ms,
                result.query_count,
                result.html_bytes / 1024,
                api_total,
            )
        )
        if result.error:
            print("  ERROR:", result.error[:120])
    return results


def write_report(label: str, results: List[PageBenchmark], path: Path) -> None:
    ranked = sorted(results, key=lambda r: r.total_ms, reverse=True)
    payload = {
        "label": label,
        "pages": [
            {
                "name": r.name,
                "path": r.path,
                "total_ms": round(r.total_ms, 2),
                "db_ms": round(r.db_ms, 2),
                "query_count": r.query_count,
                "html_bytes": r.html_bytes,
                "external_calls": r.external_calls,
                "slow_queries": [(s, round(ms, 2)) for s, ms in r.slow_queries[:5]],
                "repeated_queries": r.repeated_queries[:5],
                "connection_opens": r.connection_opens,
                "bottleneck": _top_bottleneck(r),
            }
            for r in ranked
        ],
        "slowest_5": [r.name for r in ranked[:5]],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compare_reports(before_path: Path, after_path: Path) -> None:
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())
    before_map = {p["name"]: p for p in before["pages"]}
    after_map = {p["name"]: p for p in after["pages"]}

    print("\n=== BEFORE vs AFTER ===")
    print(
        "{:<18} {:>8} {:>8} {:>8} {:>8} {:>8}".format(
            "Page", "Before", "After", "Saved", "Queries", "Conn"
        )
    )
    print("-" * 70)
    for name in before_map:
        b = before_map[name]
        a = after_map.get(name, {})
        saved = b["total_ms"] - a.get("total_ms", b["total_ms"])
        pct = (saved / b["total_ms"] * 100) if b["total_ms"] else 0
        print(
            "{:<18} {:>7.0f}ms {:>7.0f}ms {:>+7.0f}ms ({:+.0f}%) {:>+4}/{:<4} {:>+4}/{:<3}".format(
                name,
                b["total_ms"],
                a.get("total_ms", 0),
                saved,
                pct,
                a.get("query_count", 0) - b["query_count"],
                b["query_count"],
                a.get("connection_opens", 0) - b.get("connection_opens", 0),
                b.get("connection_opens", 0),
            )
        )


def compare_three_reports(
    before_path: Path,
    current_path: Path,
    after_path: Path,
) -> None:
    before = json.loads(before_path.read_text())
    current = json.loads(current_path.read_text())
    after = json.loads(after_path.read_text())
    before_map = {p["name"]: p for p in before["pages"]}
    current_map = {p["name"]: p for p in current["pages"]}
    after_map = {p["name"]: p for p in after["pages"]}

    print("\n=== BEFORE vs PHASE 1 vs PHASE 2 ===")
    print(
        "{:<16} {:>8} {:>8} {:>8} {:>6} {:>6} {:>6}".format(
            "Page",
            "Before",
            "Phase1",
            "Phase2",
            "Q0",
            "Q1",
            "Q2",
        )
    )
    print("-" * 72)
    for name in before_map:
        b = before_map[name]
        c = current_map.get(name, {})
        a = after_map.get(name, {})
        print(
            "{:<16} {:>7.0f}ms {:>7.0f}ms {:>7.0f}ms {:>6} {:>6} {:>6}".format(
                name,
                b.get("total_ms", 0),
                c.get("total_ms", 0),
                a.get("total_ms", 0),
                b.get("query_count", 0),
                c.get("query_count", 0),
                a.get("query_count", 0),
            )
        )


def check_connection_leak(client) -> dict:
    """Verify each request opens and closes exactly one DB connection."""
    import db_backend as backend

    opens = {"count": 0}
    closes = {"count": 0}
    original_open = backend._open_connection
    original_close = backend.CompatConnection.close

    def counted_open():
        opens["count"] += 1
        return original_open()

    def counted_close(self):
        closes["count"] += 1
        return original_close(self)

    backend._open_connection = counted_open
    backend.CompatConnection.close = counted_close
    try:
        for _ in range(5):
            client.get("/bookings/new")
        leak = opens["count"] - closes["count"]
        return {
            "opens": opens["count"],
            "closes": closes["count"],
            "leak": leak,
            "pass": leak == 0,
        }
    finally:
        backend._open_connection = original_open
        backend.CompatConnection.close = original_close
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())
    before_map = {p["name"]: p for p in before["pages"]}
    after_map = {p["name"]: p for p in after["pages"]}

    print("\n=== BEFORE vs AFTER ===")
    print("{:<18} {:>10} {:>10} {:>10} {:>8}".format(
        "Page", "Before", "After", "Saved", "Queries"
    ))
    print("-" * 62)
    for name in before_map:
        b = before_map[name]
        a = after_map.get(name, {})
        saved = b["total_ms"] - a.get("total_ms", b["total_ms"])
        pct = (saved / b["total_ms"] * 100) if b["total_ms"] else 0
        q_before = b["query_count"]
        q_after = a.get("query_count", q_before)
        print(
            "{:<18} {:>8.0f}ms {:>8.0f}ms {:>+8.0f}ms ({:+.0f}%) {:>+4}/{:<4}".format(
                name,
                b["total_ms"],
                a.get("total_ms", 0),
                saved,
                pct,
                q_after - q_before,
                q_before,
            )
        )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--output", default="test_results/performance/{label}.json")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    parser.add_argument(
        "--compare-three",
        nargs=3,
        metavar=("BEFORE", "PHASE1", "PHASE2"),
    )
    parser.add_argument("--leak-check", action="store_true")
    args = parser.parse_args()

    if args.compare_three:
        compare_three_reports(
            Path(args.compare_three[0]),
            Path(args.compare_three[1]),
            Path(args.compare_three[2]),
        )
        return 0

    if args.compare:
        compare_reports(Path(args.compare[0]), Path(args.compare[1]))
        return 0

    if args.leak_check:
        install_instrumentation()
        client, _ = _setup_client()
        result = check_connection_leak(client)
        print("Connection opens:", result["opens"])
        print("Connection closes:", result["closes"])
        print("Leak:", result["leak"])
        print("PASS" if result["pass"] else "FAIL")
        return 0 if result["pass"] else 1

    output = Path(args.output.format(label=args.label))
    results = run_benchmarks(args.label)
    write_report(args.label, results, output)
    print("\nReport written to", output)

    ranked = sorted(results, key=lambda r: r.total_ms, reverse=True)[:5]
    print("\nSlowest 5 pages:")
    for r in ranked:
        print(" - {0}: {1:.0f}ms, {2} queries — {3}".format(
            r.name, r.total_ms, r.query_count, _top_bottleneck(r)
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
