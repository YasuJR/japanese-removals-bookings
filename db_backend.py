"""Database backend — SQLite (local) or PostgreSQL (production via DATABASE_URL)."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import config

Params = Optional[Union[Sequence[Any], Dict[str, Any]]]

_USE_POSTGRES = bool(getattr(config, "DATABASE_URL", ""))


def is_postgres() -> bool:
    return _USE_POSTGRES


def adapt_sql(sql: str) -> str:
    if not _USE_POSTGRES:
        return sql
    text = sql
    text = text.replace("datetime('now')", "CURRENT_TIMESTAMP")
    text = re.sub(
        r"datetime\('now',\s*'\-(\d+)\s+minutes'\)",
        r"(CURRENT_TIMESTAMP - INTERVAL '\1 minutes')",
        text,
    )
    if "?" in text:
        text = text.replace("?", "%s")
    text = text.replace("INSERT OR REPLACE INTO", "INSERT INTO")
    if "INSERT INTO processed_gmail_messages" in text and "ON CONFLICT" not in text:
        text = text.replace(
            ") VALUES (",
            ") VALUES (",
        )
        if text.rstrip().endswith(")"):
            text = (
                text.rstrip()[:-1]
                + " ON CONFLICT (message_id) DO UPDATE SET "
                "booking_id = EXCLUDED.booking_id, "
                "subject = EXCLUDED.subject, "
                "processed_at = CURRENT_TIMESTAMP)"
            )
    return text


class RowLike:
    """Dict-like row for SQLite and PostgreSQL."""

    def __init__(self, mapping: Dict[str, Any]):
        self._data = dict(mapping)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __iter__(self):
        return iter(self._data)

    def __contains__(self, key):
        return key in self._data


class CompatCursorFixed:
    def __init__(self, cursor, is_postgres_backend: bool):
        self._cursor = cursor
        self._is_postgres = is_postgres_backend
        self.lastrowid: Optional[int] = None

    def _wrap(self, row):
        if row is None:
            return None
        if isinstance(row, sqlite3.Row):
            return row
        if isinstance(row, dict):
            return RowLike(row)
        if hasattr(row, "keys"):
            return RowLike(dict(row))
        columns = [desc[0] for desc in self._cursor.description or []]
        return RowLike(dict(zip(columns, row)))

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    def fetchall(self):
        return [self._wrap(row) for row in self._cursor.fetchall()]


class CompatConnection:
    def __init__(self, raw_conn, is_postgres_backend: bool):
        self._conn = raw_conn
        self._is_postgres = is_postgres_backend

    def execute(self, sql: str, params: Params = ()):
        sql_adapted = adapt_sql(sql)
        params = params or ()
        if self._is_postgres:
            import psycopg2.extras

            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            returning = False
            upper = sql_adapted.upper()
            if upper.startswith("INSERT") and "RETURNING" not in upper:
                if "INTO PROCESSED_GMAIL_MESSAGES" not in upper:
                    sql_adapted = sql_adapted.rstrip().rstrip(";") + " RETURNING id"
                    returning = True
            cur.execute(sql_adapted, params)
            wrapper = CompatCursorFixed(cur, True)
            if returning:
                row = cur.fetchone()
                wrapper.lastrowid = int(row["id"]) if row and row.get("id") is not None else None
            return wrapper
        cur = self._conn.execute(sql_adapted, params)
        wrapper = CompatCursorFixed(cur, False)
        wrapper.lastrowid = cur.lastrowid
        return wrapper

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                self.commit()
            except Exception:
                pass
        self.close()
        return False


def get_connection() -> CompatConnection:
    if _USE_POSTGRES:
        import psycopg2

        raw = psycopg2.connect(config.DATABASE_URL)
        raw.autocommit = False
        return CompatConnection(raw, True)
    db_path = Path(__file__).parent / "bookings.db"
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    return CompatConnection(raw, False)


def table_columns(conn: CompatConnection, table_name: str) -> set:
    if conn._is_postgres:
        cur = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table_name,),
        )
        return {row["column_name"] for row in cur.fetchall()}
    rows = conn.execute("PRAGMA table_info({0})".format(table_name)).fetchall()
    return {row[1] for row in rows}


def postgres_ddl() -> List[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT NOT NULL,
            pickup_address TEXT NOT NULL,
            delivery_address TEXT NOT NULL,
            move_date TEXT NOT NULL,
            num_movers INTEGER NOT NULL,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS staff_users (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS automation_log (
            id SERIAL PRIMARY KEY,
            booking_id INTEGER,
            automation_type TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS review_requests (
            id SERIAL PRIMARY KEY,
            booking_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            channel TEXT NOT NULL DEFAULT 'sms',
            status TEXT NOT NULL DEFAULT 'scheduled',
            scheduled_at TEXT NOT NULL,
            sent_at TEXT,
            clicked_at TEXT,
            reviewed_at TEXT,
            error_message TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sms_delivery_log (
            id SERIAL PRIMARY KEY,
            booking_id INTEGER,
            automation_type TEXT NOT NULL DEFAULT '',
            template_key TEXT NOT NULL DEFAULT '',
            twilio_sid TEXT NOT NULL DEFAULT '',
            to_number TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS booking_extra_charges (
            id SERIAL PRIMARY KEY,
            booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL DEFAULT 1,
            unit_price DOUBLE PRECISION NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS processed_gmail_messages (
            message_id TEXT NOT NULL PRIMARY KEY,
            booking_id INTEGER,
            subject TEXT NOT NULL DEFAULT '',
            processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS crew_members (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trucks (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            registration TEXT NOT NULL DEFAULT '',
            truck_type TEXT NOT NULL DEFAULT '',
            capacity TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS quote_rate_limit (
            id SERIAL PRIMARY KEY,
            ip_address TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS draft_leads (
            id SERIAL PRIMARY KEY,
            customer_name TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            move_date TEXT NOT NULL DEFAULT '',
            pickup_address TEXT NOT NULL DEFAULT '',
            delivery_address TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'SMS',
            confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
            raw_message TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            booking_id INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]
