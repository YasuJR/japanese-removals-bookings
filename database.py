"""SQLite or PostgreSQL storage for bookings and staff."""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import db_backend
import job_status

DB_PATH = Path(__file__).parent / "bookings.db"

BOOKING_EXTRA_COLUMNS = [
    ("google_calendar_event_id", "TEXT"),
    ("xero_invoice_id", "TEXT"),
    ("sms_last_sent_at", "TEXT"),
    ("start_time", "TEXT"),
    ("finish_time", "TEXT"),
    ("duration_hours", "TEXT"),
    ("crew", "TEXT"),
    ("hourly_rate", "REAL"),
    ("callout_fee", "REAL"),
    ("gst_enabled", "INTEGER"),
    ("payment_status", "TEXT"),
    ("invoice_status", "TEXT"),
    ("invoice_number", "TEXT"),
    ("status", "TEXT"),
    ("invoice_issue_date", "TEXT"),
    ("invoice_due_date", "TEXT"),
    ("paid_at", "TEXT"),
    ("sms_move_reminder_sent_at", "TEXT"),
    ("sms_confirmation_sent_at", "TEXT"),
    ("sms_thank_you_sent_at", "TEXT"),
    ("sms_payment_reminder_sent_at", "TEXT"),
    ("invoice_custom_text", "TEXT"),
    ("invoice_bank_account_name", "TEXT"),
    ("invoice_bank_bsb", "TEXT"),
    ("invoice_bank_account", "TEXT"),
    ("stripe_checkout_session_id", "TEXT"),
    ("stripe_payment_intent_id", "TEXT"),
    ("stripe_payment_status", "TEXT"),
    ("stripe_surcharge_amount", "REAL"),
    ("stripe_total_charged", "REAL"),
    ("xero_payment_id", "TEXT"),
    ("sms_payment_confirmation_sent_at", "TEXT"),
    ("sms_booking_confirmed_sent_at", "TEXT"),
    ("calendar_confirmed_synced_at", "TEXT"),
    ("staff_notification_sent_at", "TEXT"),
    ("on_route_at", "TEXT"),
    ("eta_sms_sent_at", "TEXT"),
    ("eta_minutes", "INTEGER"),
    ("driver_name", "TEXT"),
    ("completed_at", "TEXT"),
    ("review_request_scheduled_at", "TEXT"),
    ("review_request_sent_at", "TEXT"),
    ("review_request_cancelled_at", "TEXT"),
    ("double_booking_override_at", "TEXT"),
    ("gmail_message_id", "TEXT"),
    ("xero_invoice_automation_error", "TEXT"),
    ("staff_cost", "REAL"),
    ("fuel_cost", "REAL"),
    ("truck_cost", "REAL"),
    ("other_costs", "REAL"),
    ("profit_crew_hours", "REAL"),
    ("profit_hourly_wage", "REAL"),
    ("stripe_fee", "REAL"),
    ("gst_amount", "REAL"),
    ("net_revenue", "REAL"),
    ("estimated_profit", "REAL"),
    ("profit_margin_percent", "REAL"),
    ("payment_reminder_1_sent_at", "TEXT"),
    ("payment_reminder_2_sent_at", "TEXT"),
    ("payment_reminder_3_sent_at", "TEXT"),
    ("payment_reminders_cancelled_at", "TEXT"),
    ("truck_assigned", "TEXT"),
    ("source", "TEXT"),
]


def get_connection():
    return db_backend.get_connection()


def _ensure_columns(conn) -> None:
    existing = db_backend.table_columns(conn, "bookings")
    for name, col_type in BOOKING_EXTRA_COLUMNS:
        if name not in existing:
            conn.execute(
                "ALTER TABLE bookings ADD COLUMN {0} {1}".format(name, col_type)
            )


def _seed_crew_and_trucks(conn) -> None:
    crew_count = conn.execute("SELECT COUNT(*) AS c FROM crew_members").fetchone()["c"]
    if int(crew_count) == 0:
        defaults = [
            ("Yasu", "0411111111", "Driver"),
            ("Tom", "0422222222", "Driver"),
            ("Ken", "0433333333", "Driver"),
        ]
        for name, phone, role in defaults:
            conn.execute(
                """
                INSERT INTO crew_members (name, phone, role, active)
                VALUES (?, ?, ?, 1)
                """,
                (name, phone, role),
            )
    truck_count = conn.execute("SELECT COUNT(*) AS c FROM trucks").fetchone()["c"]
    if int(truck_count) == 0:
        defaults = [
            ("Truck 1", "1ABC123", "Medium", "30 m³"),
            ("Truck 2", "2DEF456", "Large", "45 m³"),
        ]
        for name, registration, truck_type, capacity in defaults:
            conn.execute(
                """
                INSERT INTO trucks (name, registration, truck_type, capacity, active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (name, registration, truck_type, capacity),
            )


def init_db() -> None:
    if db_backend.is_postgres():
        with get_connection() as conn:
            for ddl in db_backend.postgres_ddl():
                conn.execute(ddl)
            _ensure_columns(conn)
            _seed_crew_and_trucks(conn)
        return
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT NOT NULL,
                pickup_address TEXT NOT NULL,
                delivery_address TEXT NOT NULL,
                move_date TEXT NOT NULL,
                num_movers INTEGER NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_id INTEGER,
                automation_type TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_id INTEGER NOT NULL,
                token TEXT NOT NULL UNIQUE,
                channel TEXT NOT NULL DEFAULT 'sms',
                status TEXT NOT NULL DEFAULT 'scheduled',
                scheduled_at TEXT NOT NULL,
                sent_at TEXT,
                clicked_at TEXT,
                reviewed_at TEXT,
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sms_delivery_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_id INTEGER,
                automation_type TEXT NOT NULL DEFAULT '',
                template_key TEXT NOT NULL DEFAULT '',
                twilio_sid TEXT NOT NULL DEFAULT '',
                to_number TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS booking_extra_charges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 1,
                unit_price REAL NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_gmail_messages (
                message_id TEXT NOT NULL PRIMARY KEY,
                booking_id INTEGER,
                subject TEXT NOT NULL DEFAULT '',
                processed_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crew_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                phone TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trucks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                registration TEXT NOT NULL DEFAULT '',
                truck_type TEXT NOT NULL DEFAULT '',
                capacity TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quote_rate_limit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS draft_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                move_date TEXT NOT NULL DEFAULT '',
                pickup_address TEXT NOT NULL DEFAULT '',
                delivery_address TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'SMS',
                confidence REAL NOT NULL DEFAULT 0,
                raw_message TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                booking_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        _ensure_columns(conn)
        _seed_crew_and_trucks(conn)
        conn.commit()


def create_staff_user(username: str, password_hash: str, display_name: str = "") -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO staff_users (username, password_hash, display_name)
            VALUES (?, ?, ?)
            """,
            (username.strip().lower(), password_hash, display_name or username),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_staff_by_username(username: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM staff_users WHERE username = ?",
            (username.strip().lower(),),
        ).fetchone()


def get_staff_user(user_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, username, display_name, created_at FROM staff_users WHERE id = ?",
            (user_id,),
        ).fetchone()


def staff_user_count() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM staff_users").fetchone()
    return int(row["c"]) if row else 0


def create_booking(
    customer_name: str,
    phone: str,
    email: str,
    pickup_address: str,
    delivery_address: str,
    move_date: str,
    num_movers: int,
    notes: str,
    start_time: str = "",
    finish_time: str = "",
    duration_hours: str = "",
    crew: str = "",
    hourly_rate: float = 0.0,
    callout_fee: float = 0.0,
    gst_enabled: int = 1,
    payment_status: str = "Unpaid",
    invoice_status: str = "",
    status: str = job_status.DEFAULT_STATUS,
    gmail_message_id: str = "",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO bookings (
                customer_name, phone, email,
                pickup_address, delivery_address,
                move_date, num_movers, notes,
                start_time, finish_time, duration_hours, crew,
                hourly_rate, callout_fee, gst_enabled,
                payment_status, invoice_status, status,
                gmail_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_name.strip(),
                phone.strip(),
                email.strip(),
                pickup_address.strip(),
                delivery_address.strip(),
                move_date,
                num_movers,
                (notes or "").strip(),
                (start_time or "").strip(),
                (finish_time or "").strip(),
                (duration_hours or "").strip(),
                (crew or "").strip(),
                hourly_rate,
                callout_fee,
                int(gst_enabled),
                (payment_status or "Unpaid").strip(),
                (invoice_status or "").strip(),
                job_status.normalize(status),
                (gmail_message_id or "").strip(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_booking(
    booking_id: int,
    customer_name: str,
    phone: str,
    email: str,
    pickup_address: str,
    delivery_address: str,
    move_date: str,
    num_movers: int,
    notes: str,
    start_time: str = "",
    finish_time: str = "",
    duration_hours: str = "",
    crew: str = "",
    hourly_rate: float = 0.0,
    callout_fee: float = 0.0,
    gst_enabled: int = 1,
    payment_status: str = "Unpaid",
    invoice_status: str = "",
    status: str = job_status.DEFAULT_STATUS,
) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE bookings SET
                customer_name = ?,
                phone = ?,
                email = ?,
                pickup_address = ?,
                delivery_address = ?,
                move_date = ?,
                num_movers = ?,
                notes = ?,
                start_time = ?,
                finish_time = ?,
                duration_hours = ?,
                crew = ?,
                hourly_rate = ?,
                callout_fee = ?,
                gst_enabled = ?,
                payment_status = ?,
                invoice_status = ?,
                status = ?
            WHERE id = ?
            """,
            (
                customer_name.strip(),
                phone.strip(),
                email.strip(),
                pickup_address.strip(),
                delivery_address.strip(),
                move_date,
                num_movers,
                (notes or "").strip(),
                (start_time or "").strip(),
                (finish_time or "").strip(),
                (duration_hours or "").strip(),
                (crew or "").strip(),
                hourly_rate,
                callout_fee,
                int(gst_enabled),
                (payment_status or "Unpaid").strip(),
                (invoice_status or "").strip(),
                job_status.normalize(status),
                booking_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_booking_invoice_fields(
    booking_id: int, fields: Dict[str, Any]
) -> None:
    allowed = {
        "hourly_rate",
        "callout_fee",
        "gst_enabled",
        "payment_status",
        "invoice_status",
        "invoice_number",
        "xero_invoice_id",
        "invoice_issue_date",
        "invoice_due_date",
        "paid_at",
        "invoice_custom_text",
        "invoice_bank_account_name",
        "invoice_bank_bsb",
        "invoice_bank_account",
        "stripe_checkout_session_id",
        "stripe_payment_intent_id",
        "stripe_payment_status",
        "stripe_surcharge_amount",
        "stripe_total_charged",
        "xero_payment_id",
        "xero_invoice_automation_error",
    }
    parts = []
    values = []
    for key, val in fields.items():
        if key in allowed:
            parts.append("{0} = ?".format(key))
            values.append(val)
    if not parts:
        return
    values.append(booking_id)
    sql = "UPDATE bookings SET " + ", ".join(parts) + " WHERE id = ?"
    with get_connection() as conn:
        conn.execute(sql, values)
        conn.commit()


def update_booking_profit_fields(
    booking_id: int, fields: Dict[str, Any]
) -> None:
    allowed = {
        "staff_cost",
        "fuel_cost",
        "truck_cost",
        "other_costs",
        "profit_crew_hours",
        "profit_hourly_wage",
        "stripe_fee",
        "gst_amount",
        "net_revenue",
        "estimated_profit",
        "profit_margin_percent",
    }
    parts = []
    values = []
    for key, val in fields.items():
        if key in allowed:
            parts.append("{0} = ?".format(key))
            values.append(val)
    if not parts:
        return
    values.append(booking_id)
    sql = "UPDATE bookings SET " + ", ".join(parts) + " WHERE id = ?"
    with get_connection() as conn:
        conn.execute(sql, values)
        conn.commit()


def update_booking_integration_fields(
    booking_id: int, fields: Dict[str, Any]
) -> None:
    allowed = {
        "google_calendar_event_id",
        "xero_invoice_id",
        "sms_last_sent_at",
        "sms_move_reminder_sent_at",
        "sms_confirmation_sent_at",
        "sms_thank_you_sent_at",
        "sms_payment_reminder_sent_at",
        "sms_payment_confirmation_sent_at",
        "sms_booking_confirmed_sent_at",
        "calendar_confirmed_synced_at",
        "staff_notification_sent_at",
        "on_route_at",
        "eta_sms_sent_at",
        "eta_minutes",
        "driver_name",
        "completed_at",
        "review_request_scheduled_at",
        "review_request_sent_at",
        "review_request_cancelled_at",
        "double_booking_override_at",
        "payment_reminder_1_sent_at",
        "payment_reminder_2_sent_at",
        "payment_reminder_3_sent_at",
        "payment_reminders_cancelled_at",
        "truck_assigned",
        "source",
    }
    parts = []
    values = []
    for key, val in fields.items():
        if key in allowed:
            parts.append("{0} = ?".format(key))
            values.append(val)
    if not parts:
        return
    values.append(booking_id)
    sql = "UPDATE bookings SET " + ", ".join(parts) + " WHERE id = ?"
    with get_connection() as conn:
        conn.execute(sql, values)
        conn.commit()


def update_booking_on_route_fields(
    booking_id: int, fields: Dict[str, Any]
) -> None:
    update_booking_integration_fields(booking_id, fields)


def update_booking_status(booking_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE bookings SET status = ? WHERE id = ?",
            (job_status.normalize(status), booking_id),
        )
        conn.commit()


def add_automation_log(
    automation_type: str,
    status: str,
    message: str,
    booking_id: Optional[int] = None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO automation_log (
                booking_id, automation_type, status, message
            ) VALUES (?, ?, ?, ?)
            """,
            (
                booking_id,
                automation_type.strip(),
                status.strip(),
                (message or "").strip(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_automation_logs(
    limit: int = 50,
    automation_types: Optional[tuple] = None,
) -> List[Dict[str, Any]]:
    limit = max(int(limit), 1)
    with get_connection() as conn:
        if automation_types:
            placeholders = ", ".join("?" for _ in automation_types)
            rows = conn.execute(
                """
                SELECT id, booking_id, automation_type, status, message, created_at
                FROM automation_log
                WHERE automation_type IN ({0})
                ORDER BY id DESC
                LIMIT ?
                """.format(
                    placeholders
                ),
                (*automation_types, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, booking_id, automation_type, status, message, created_at
                FROM automation_log
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def add_sms_delivery_log(
    booking_id: Optional[int],
    automation_type: str,
    template_key: str,
    twilio_sid: str,
    to_number: str,
    body: str,
    status: str,
    error_message: str = "",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sms_delivery_log (
                booking_id, automation_type, template_key, twilio_sid,
                to_number, body, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                booking_id,
                (automation_type or "").strip(),
                (template_key or "").strip(),
                (twilio_sid or "").strip(),
                (to_number or "").strip(),
                body or "",
                (status or "queued").strip().lower(),
                (error_message or "").strip(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_sms_delivery_by_sid(
    twilio_sid: str,
    status: str,
    error_message: str = "",
) -> bool:
    if not (twilio_sid or "").strip():
        return False
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE sms_delivery_log
            SET status = ?, error_message = ?, updated_at = datetime('now')
            WHERE twilio_sid = ?
            """,
            (
                (status or "").strip().lower(),
                (error_message or "").strip(),
                twilio_sid.strip(),
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def list_sms_delivery_logs(limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, booking_id, automation_type, template_key, twilio_sid,
                   to_number, body, status, error_message, created_at, updated_at
            FROM sms_delivery_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(int(limit), 1),),
        ).fetchall()
    return [dict(row) for row in rows]


def create_review_request(
    booking_id: int,
    token: str,
    channel: str,
    scheduled_at: str,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO review_requests (
                booking_id, token, channel, status, scheduled_at
            ) VALUES (?, ?, ?, 'scheduled', ?)
            """,
            (booking_id, token.strip(), channel.strip(), scheduled_at),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_review_request_by_token(token: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM review_requests WHERE token = ?",
            (token.strip(),),
        ).fetchone()


def get_review_request_for_booking(booking_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM review_requests
            WHERE booking_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (booking_id,),
        ).fetchone()


def get_active_review_request_for_booking(booking_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM review_requests
            WHERE booking_id = ?
              AND status IN ('scheduled', 'sent', 'clicked')
            ORDER BY id DESC
            LIMIT 1
            """,
            (booking_id,),
        ).fetchone()


def list_due_review_requests(now_iso: str) -> List[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM review_requests
            WHERE status = 'scheduled' AND scheduled_at <= ?
            ORDER BY scheduled_at ASC
            """,
            (now_iso,),
        ).fetchall()
    return list(rows)


def list_review_requests(limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT r.*, b.customer_name
            FROM review_requests r
            LEFT JOIN bookings b ON b.id = r.booking_id
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (max(int(limit), 1),),
        ).fetchall()
    return [dict(row) for row in rows]


def update_review_request_status(
    request_id: int,
    status: str,
    *,
    channel: Optional[str] = None,
    sent_at: Optional[str] = None,
    error: str = "",
) -> None:
    parts = ["status = ?"]
    values: List[Any] = [status.strip()]
    if channel is not None:
        parts.append("channel = ?")
        values.append(channel.strip())
    if sent_at is not None:
        parts.append("sent_at = ?")
        values.append(sent_at)
    if error:
        parts.append("error_message = ?")
        values.append(error.strip())
    values.append(request_id)
    sql = "UPDATE review_requests SET " + ", ".join(parts) + " WHERE id = ?"
    with get_connection() as conn:
        conn.execute(sql, values)
        conn.commit()


def cancel_review_request(request_id: int) -> bool:
    """Cancel a scheduled review request (not yet sent)."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE review_requests
            SET status = 'cancelled'
            WHERE id = ? AND status = 'scheduled'
            """,
            (request_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def mark_review_request_clicked(request_id: int, clicked_at: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE review_requests
            SET status = 'clicked', clicked_at = ?
            WHERE id = ? AND status = 'sent'
            """,
            (clicked_at, request_id),
        )
        conn.commit()


def review_funnel_stats() -> Dict[str, int]:
    with get_connection() as conn:
        sent = conn.execute(
            """
            SELECT COUNT(*) AS c FROM review_requests
            WHERE sent_at IS NOT NULL AND sent_at != ''
            """
        ).fetchone()
        clicked = conn.execute(
            """
            SELECT COUNT(*) AS c FROM review_requests
            WHERE clicked_at IS NOT NULL AND clicked_at != ''
            """
        ).fetchone()
        received = conn.execute(
            """
            SELECT COUNT(*) AS c FROM review_requests
            WHERE status = 'reviewed'
            """
        ).fetchone()
    return {
        "sent": int(sent["c"]) if sent else 0,
        "clicked": int(clicked["c"]) if clicked else 0,
        "received": int(received["c"]) if received else 0,
    }


def list_created_between(start_date: str, end_date: str) -> List[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM bookings
            WHERE date(created_at) >= date(?)
              AND date(created_at) <= date(?)
            ORDER BY created_at DESC, id DESC
            """,
            (start_date, end_date),
        ).fetchall()
    return list(rows)


def count_review_requests_by_status(status: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM review_requests WHERE status = ?",
            (status.strip(),),
        ).fetchone()
    return int(row["c"]) if row else 0


def average_review_rating() -> Optional[float]:
    """Placeholder for per-review ratings (use Google stats in review settings)."""
    return None


def mark_review_request_reviewed(request_id: int, reviewed_at: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE review_requests
            SET status = 'reviewed', reviewed_at = ?
            WHERE id = ?
            """,
            (reviewed_at, request_id),
        )
        conn.commit()


def delete_booking(booking_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        conn.commit()
        return cursor.rowcount > 0


def list_by_date(move_date: str) -> List[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM bookings
            WHERE move_date = ?
            ORDER BY start_time ASC, id ASC
            """,
            (move_date,),
        ).fetchall()
    return list(rows)


def list_between_dates(start_date: str, end_date: str) -> List[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM bookings
            WHERE move_date >= ? AND move_date <= ?
            ORDER BY move_date ASC, start_time ASC, id ASC
            """,
            (start_date, end_date),
        ).fetchall()
    return list(rows)


def sum_movers_between_dates(start_date: str, end_date: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(num_movers), 0) AS total
            FROM bookings
            WHERE move_date >= ? AND move_date <= ?
            """,
            (start_date, end_date),
        ).fetchone()
    return int(row["total"]) if row else 0


def list_upcoming(today: Optional[str] = None) -> List[sqlite3.Row]:
    if today is None:
        today = date.today().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM bookings
            WHERE move_date >= ?
            ORDER BY move_date ASC, id ASC
            """,
            (today,),
        ).fetchall()
    return list(rows)


def list_all() -> List[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings ORDER BY move_date DESC, id DESC"
        ).fetchall()
    return list(rows)


def _dashboard_sort_key(row: sqlite3.Row) -> tuple:
    return (
        job_status.sort_priority(row["status"]),
        row["move_date"] or "",
        row["start_time"] or "",
        int(row["id"]),
    )


def list_for_dashboard(filter_name: str, today_iso: str) -> List[sqlite3.Row]:
    """Filtered booking list with dashboard status sort order."""
    filter_name = (filter_name or "all").strip().lower()
    sql = "SELECT * FROM bookings"
    params: List[Any] = []
    clauses: List[str] = []

    if filter_name == "today":
        clauses.append("move_date = ?")
        params.append(today_iso)
    elif filter_name == "upcoming":
        clauses.append("move_date >= ?")
        params.append(today_iso)
        clauses.append(
            "COALESCE(status, ?) NOT IN ('Completed', 'Paid', 'Cancelled')"
        )
        params.append(job_status.DEFAULT_STATUS)
    elif filter_name == "completed":
        clauses.append("COALESCE(status, ?) = 'Completed'")
        params.append(job_status.DEFAULT_STATUS)
    elif filter_name == "paid":
        clauses.append("COALESCE(status, ?) = 'Paid'")
        params.append(job_status.DEFAULT_STATUS)
    elif filter_name == "cancelled":
        clauses.append("COALESCE(status, ?) = 'Cancelled'")
        params.append(job_status.DEFAULT_STATUS)

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    return sorted(list(rows), key=_dashboard_sort_key)


def get_booking(booking_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (booking_id,)
        ).fetchone()


def get_booking_by_stripe_session(session_id: str) -> Optional[sqlite3.Row]:
    sid = (session_id or "").strip()
    if not sid:
        return None
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM bookings WHERE stripe_checkout_session_id = ? LIMIT 1",
            (sid,),
        ).fetchone()


def search_bookings(query: str) -> List[sqlite3.Row]:
    q = (query or "").strip()
    if not q:
        return list_all()

    safe = q.replace("%", "").replace("_", "")
    pattern = "%" + safe + "%"
    clauses = [
        "customer_name LIKE ?",
        "phone LIKE ?",
        "email LIKE ?",
        "pickup_address LIKE ?",
        "delivery_address LIKE ?",
        "move_date LIKE ?",
        "notes LIKE ?",
        "CAST(id AS TEXT) LIKE ?",
    ]
    params = [pattern] * len(clauses)

    if q.isdigit():
        clauses.insert(0, "id = ?")
        params.insert(0, int(q))

    where_sql = " OR ".join(clauses)
    sql = (
        "SELECT * FROM bookings WHERE "
        + where_sql
        + " ORDER BY move_date DESC, id DESC"
    )

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return list(rows)


def list_extra_charges(booking_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, booking_id, description, quantity, unit_price, sort_order
            FROM booking_extra_charges
            WHERE booking_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (booking_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def replace_extra_charges(booking_id: int, items: List[Dict[str, Any]]) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM booking_extra_charges WHERE booking_id = ?",
            (booking_id,),
        )
        for index, item in enumerate(items):
            conn.execute(
                """
                INSERT INTO booking_extra_charges (
                    booking_id, description, quantity, unit_price, sort_order
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    booking_id,
                    str(item.get("description") or "").strip(),
                    float(item.get("quantity") or 1),
                    float(item.get("unit_price") or 0),
                    index,
                ),
            )
        conn.commit()


def is_gmail_message_processed(message_id: str) -> bool:
    text = (message_id or "").strip()
    if not text:
        return False
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_gmail_messages WHERE message_id = ?",
            (text,),
        ).fetchone()
    return row is not None


def mark_gmail_message_processed(
    message_id: str,
    booking_id: Optional[int] = None,
    subject: str = "",
) -> None:
    text = (message_id or "").strip()
    if not text:
        return
    with get_connection() as conn:
        if db_backend.is_postgres():
            conn.execute(
                """
                INSERT INTO processed_gmail_messages (
                    message_id, booking_id, subject, processed_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (message_id) DO UPDATE SET
                    booking_id = EXCLUDED.booking_id,
                    subject = EXCLUDED.subject,
                    processed_at = CURRENT_TIMESTAMP
                """,
                (text, booking_id, (subject or "").strip()),
            )
        else:
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_gmail_messages (
                    message_id, booking_id, subject, processed_at
                ) VALUES (?, ?, ?, datetime('now'))
                """,
                (text, booking_id, (subject or "").strip()),
            )
        conn.commit()


def list_processed_gmail_messages(limit: int = 30) -> List[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.message_id, p.booking_id, p.subject, p.processed_at,
                   b.customer_name
            FROM processed_gmail_messages p
            LEFT JOIN bookings b ON b.id = p.booking_id
            ORDER BY p.processed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_crew_members(active_only: bool = False) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        if active_only:
            rows = conn.execute(
                """
                SELECT id, name, phone, role, active, created_at
                FROM crew_members
                WHERE active = 1
                ORDER BY name ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, name, phone, role, active, created_at
                FROM crew_members
                ORDER BY name ASC
                """
            ).fetchall()
    return [dict(row) for row in rows]


def get_crew_member(crew_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM crew_members WHERE id = ?",
            (crew_id,),
        ).fetchone()


def create_crew_member(
    name: str,
    phone: str = "",
    role: str = "",
    active: int = 1,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO crew_members (name, phone, role, active)
            VALUES (?, ?, ?, ?)
            """,
            (name.strip(), (phone or "").strip(), (role or "").strip(), int(active)),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_crew_member(
    crew_id: int,
    name: str,
    phone: str = "",
    role: str = "",
    active: int = 1,
) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE crew_members
            SET name = ?, phone = ?, role = ?, active = ?
            WHERE id = ?
            """,
            (
                name.strip(),
                (phone or "").strip(),
                (role or "").strip(),
                int(active),
                crew_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def list_trucks(active_only: bool = False) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        if active_only:
            rows = conn.execute(
                """
                SELECT id, name, registration, truck_type, capacity, active, created_at
                FROM trucks
                WHERE active = 1
                ORDER BY name ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, name, registration, truck_type, capacity, active, created_at
                FROM trucks
                ORDER BY name ASC
                """
            ).fetchall()
    return [dict(row) for row in rows]


def get_truck(truck_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM trucks WHERE id = ?",
            (truck_id,),
        ).fetchone()


def create_truck(
    name: str,
    registration: str = "",
    truck_type: str = "",
    capacity: str = "",
    active: int = 1,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trucks (name, registration, truck_type, capacity, active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                name.strip(),
                (registration or "").strip(),
                (truck_type or "").strip(),
                (capacity or "").strip(),
                int(active),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_truck(
    truck_id: int,
    name: str,
    registration: str = "",
    truck_type: str = "",
    capacity: str = "",
    active: int = 1,
) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE trucks
            SET name = ?, registration = ?, truck_type = ?, capacity = ?, active = ?
            WHERE id = ?
            """,
            (
                name.strip(),
                (registration or "").strip(),
                (truck_type or "").strip(),
                (capacity or "").strip(),
                int(active),
                truck_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def record_quote_submission(ip_address: str) -> None:
    text = (ip_address or "").strip() or "unknown"
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO quote_rate_limit (ip_address) VALUES (?)",
            (text,),
        )
        conn.commit()


def quote_submission_count_recent(ip_address: str, minutes: int = 15) -> int:
    text = (ip_address or "").strip() or "unknown"
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM quote_rate_limit
            WHERE ip_address = ?
              AND datetime(created_at) >= datetime('now', ?)
            """,
        ).fetchone()
    return int(row["c"]) if row else 0


def create_draft_lead(
    customer_name: str = "",
    phone: str = "",
    email: str = "",
    move_date: str = "",
    pickup_address: str = "",
    delivery_address: str = "",
    notes: str = "",
    source: str = "SMS",
    confidence: float = 0.0,
    raw_message: str = "",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO draft_leads (
                customer_name, phone, email, move_date,
                pickup_address, delivery_address, notes,
                source, confidence, raw_message, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (
                (customer_name or "").strip(),
                (phone or "").strip(),
                (email or "").strip(),
                (move_date or "").strip(),
                (pickup_address or "").strip(),
                (delivery_address or "").strip(),
                (notes or "").strip(),
                (source or "SMS").strip(),
                float(confidence),
                (raw_message or "").strip(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_draft_leads(status: str = "draft", limit: int = 100) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, customer_name, phone, email, move_date,
                   pickup_address, delivery_address, notes, source,
                   confidence, raw_message, status, booking_id, created_at
            FROM draft_leads
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            ((status or "draft").strip(), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def get_draft_lead(lead_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM draft_leads WHERE id = ?",
            (lead_id,),
        ).fetchone()


def mark_lead_converted(lead_id: int, booking_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE draft_leads
            SET status = 'converted', booking_id = ?
            WHERE id = ?
            """,
            (booking_id, lead_id),
        )
        conn.commit()
        return cursor.rowcount > 0
