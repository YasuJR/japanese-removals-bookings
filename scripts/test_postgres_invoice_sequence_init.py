#!/usr/bin/env python3
"""Verify invoice_sequence startup is safe on PostgreSQL-style inserts."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db_backend


class _FakeCursor:
    def __init__(self):
        self.commands = []
        self.description = None

    def execute(self, sql, params=()):
        self.commands.append((sql.strip(), params))
        if "SELECT lastval()" in sql:
            raise Exception("lastval is not yet defined in this session")

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self):
        self.cursor_obj = _FakeCursor()

    def cursor(self, cursor_factory=None):
        return self.cursor_obj

    def commit(self):
        pass

    def close(self):
        pass


def test_invoice_sequence_insert_skips_lastval():
    fake = _FakeConn()
    conn = db_backend.CompatConnection(fake, True)
    conn.execute(
        """
        INSERT INTO invoice_sequence (id, next_number)
        VALUES (1, 1)
        ON CONFLICT (id) DO NOTHING
        """
    )
    sqls = [cmd[0] for cmd in fake.cursor_obj.commands]
    assert not any("lastval" in sql.lower() for sql in sqls), sqls
    assert not any("RETURNING id" in sql for sql in sqls), sqls
    conn.execute(
        """
        SELECT invoice_number FROM bookings
        WHERE invoice_number IS NOT NULL
        """
    )
    assert len(fake.cursor_obj.commands) == 2


def test_on_conflict_insert_rolls_back_lastval_without_aborting():
    fake = _FakeConn()
    conn = db_backend.CompatConnection(fake, True)
    with patch.object(db_backend, "is_postgres", return_value=True):
        conn.execute(
            """
            INSERT INTO example_table (name)
            VALUES (?)
            ON CONFLICT (name) DO NOTHING
            """,
            ("duplicate",),
        )
    conn.execute("SELECT 1")
    sqls = [cmd[0] for cmd in fake.cursor_obj.commands]
    assert "SELECT 1" in sqls[-1]


def main():
    tests = [
        test_invoice_sequence_insert_skips_lastval,
        test_on_conflict_insert_rolls_back_lastval_without_aborting,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS:", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", fn.__name__, exc)
    print("\n{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
