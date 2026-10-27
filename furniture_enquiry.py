"""Website enquiry hand-off for furniture lists (flow A).

The WordPress site sends the quote enquiry. Only then does it fetch the list PDF,
server to server: GET /api/enquiry-list/<FL-ID>.pdf with header X-JR-Enquiry-Key.
That fetch is the moment the list is "received" (status Submitted). Saving the list
itself never notifies anyone.

Checks so a list can only travel with its own enquiry:
* the shared secret (constant-time compare); the FL-ID alone never returns data;
* only lists created on the website (source=website) — lists made for an existing
  booking (flow B) are never served;
* a list is claimed by the first enquiry; re-fetches (resend / retry of the same
  enquiry) are allowed for ENQUIRY_REFETCH_HOURS, after that it is refused (409),
  so a reference can't be reused with a later, different enquiry;
* per-IP rate limits, and every request is logged in furniture_api_log.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import config
import database as db
import furniture_lists

REQUESTS_PER_IP_PER_HOUR = 60
BAD_KEYS_PER_IP_PER_HOUR = 10
ENQUIRY_REFETCH_HOURS = 24
EDITOR = "website enquiry"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(text or ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def log_request(ip: str, public_id: str, status: int, outcome: str) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO furniture_api_log (created_at, ip, public_id, status_code, outcome) "
            "VALUES (?, ?, ?, ?, ?)",
            (_iso(_now()), (ip or "")[:64], (public_id or "")[:20], int(status), outcome[:60]),
        )
        conn.commit()


def _recent(ip: str, only_bad_key: bool) -> int:
    since = _iso(_now() - timedelta(hours=1))
    sql = "SELECT COUNT(*) AS n FROM furniture_api_log WHERE ip = ? AND created_at >= ?"
    if only_bad_key:
        sql += " AND status_code = 401"
    with db.get_connection() as conn:
        row = conn.execute(sql, (ip or "", since)).fetchone()
    return int(dict(row)["n"] or 0)


def key_ok(sent: Any) -> bool:
    expected = config.furniture_enquiry_key()
    if not expected or not sent:
        return False
    return hmac.compare_digest(str(sent).encode("utf-8"), expected.encode("utf-8"))


def check_request(ip: str, raw_id: Any, sent_key: Any) -> Tuple[int, str, Optional[Dict[str, Any]]]:
    """Return (http status, outcome, list row). 200 only when the PDF may be served."""
    if not config.furniture_enquiry_key():
        return 503, "not configured", None
    if _recent(ip, only_bad_key=True) >= BAD_KEYS_PER_IP_PER_HOUR or _recent(ip, False) >= REQUESTS_PER_IP_PER_HOUR:
        return 429, "rate limited", None
    if not key_ok(sent_key):
        return 401, "bad key", None
    public_id = furniture_lists.normalize_public_id(raw_id)
    row = furniture_lists.get_by_public_id(public_id) if public_id else None
    if not row:
        return 404, "unknown", None
    if row.get("source") != furniture_lists.SOURCE_WEBSITE:
        return 404, "not a website list", None  # booking-link lists (flow B) are never served
    received = _parse(row.get("enquiry_received_at"))
    if received and _now() - received > timedelta(hours=ENQUIRY_REFETCH_HOURS):
        return 409, "already used by an earlier enquiry", row
    return 200, "refetch" if received else "received", row


def mark_received(row: Dict[str, Any]) -> None:
    """First fetch = the enquiry was sent: mark the list Submitted. Later fetches only count."""
    now = _iso(_now())
    with db.get_connection() as conn:
        if row.get("enquiry_received_at"):
            conn.execute(
                "UPDATE furniture_lists SET enquiry_fetch_count = enquiry_fetch_count + 1 WHERE id = ?",
                (int(row["id"]),),
            )
        else:
            conn.execute(
                "UPDATE furniture_lists SET enquiry_received_at = ?, enquiry_fetch_count = 1, "
                "status = ?, submitted_at = ?, last_edited_by = ? WHERE id = ?",
                (now, furniture_lists.STATUS_SUBMITTED, row.get("submitted_at") or now, EDITOR, int(row["id"])),
            )
        conn.commit()
