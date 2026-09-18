#!/usr/bin/env python3
"""Guardrails for PostgreSQL init_db DDL (no SQLite-only syntax on prod)."""

from __future__ import annotations

import sys
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db
import db_backend


def test_booking_crew_hours_postgres_ddl_has_no_sqlite_autoincrement():
    recorded: list[str] = []

    class _Conn:
        def execute(self, sql: str, params=()):
            recorded.append(sql)

    with mock.patch.object(db_backend, "is_postgres", return_value=True):
        db._ensure_booking_crew_hours_table(_Conn())

    create_stmts = [s for s in recorded if "booking_crew_hours" in s and "CREATE TABLE" in s]
    assert create_stmts, "expected CREATE TABLE for booking_crew_hours"
    ddl = create_stmts[0]
    assert "AUTOINCREMENT" not in ddl.upper()
    assert "SERIAL" in ddl.upper()


def main() -> int:
    test_booking_crew_hours_postgres_ddl_has_no_sqlite_autoincrement()
    print("PASS: test_booking_crew_hours_postgres_ddl_has_no_sqlite_autoincrement")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
