"""Customer furniture lists: storage, access tokens, photos and booking links.

Access model
- Customer: only via the unguessable ``access_token`` in /furniture/<token>.
  The token gives access to that one list (and its photos) and nothing else.
- ``public_id`` (e.g. FL-7K3Q9P) is a short reference for enquiries/emails. It
  grants no access on its own; only office users can look a list up by it.
- Office (login_required): every list, edit, link/unlink to bookings.
- Staff Portal: a signed, expiring per-booking link (``staff_link_token``)
  that only shows lists for jobs the staff member can see in the portal.
"""

from __future__ import annotations

import io
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from zoneinfo import ZoneInfo

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import config
import database as db
import furniture_catalog as catalog

STATUS_NOT_STARTED = "not_started"
STATUS_IN_PROGRESS = "in_progress"
STATUS_SUBMITTED = "submitted"
STATUS_LABELS = {
    STATUS_NOT_STARTED: "Not started",
    STATUS_IN_PROGRESS: "In progress",
    STATUS_SUBMITTED: "Submitted",
}

SOURCE_BOOKING = "booking"
SOURCE_WEBSITE = "website"

MAX_PHOTOS = 30
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
PHOTO_MAX_EDGE = 2000
PHOTO_JPEG_QUALITY = 82
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "MPO"}
WEBSITE_LISTS_PER_IP_PER_HOUR = 20
STAFF_LINK_MAX_AGE = 60 * 60 * 24 * 30

RETURN_HOSTS = {"japaneseremovals.com.au", "www.japaneseremovals.com.au"}

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,64}$")
_PUBLIC_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PUBLIC_ID_RE = re.compile(r"\bFL[\s-]?([A-HJ-NP-Z2-9]{6})\b", re.IGNORECASE)

_LIST_COLUMNS = (
    "id, public_id, access_token, booking_id, source, status, contact_name, "
    "contact_email, contact_phone, contact_move_date, return_url, content_json, "
    "estimated_m3, item_count, photo_count, created_at, updated_at, submitted_at, "
    "last_edited_by"
)


class FurnitureError(ValueError):
    """User-facing validation error."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_perth(iso_text: Any) -> str:
    text = str(iso_text or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return text
    local = moment.astimezone(ZoneInfo(config.TIMEZONE))
    return local.strftime("%a %-d %b %Y, %-I:%M %p")


def _row_dict(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    item = dict(row)
    try:
        item["content"] = catalog.sanitize_content(json.loads(item.get("content_json") or "{}"))
    except (TypeError, ValueError):
        item["content"] = catalog.sanitize_content({})
    if not item["content"]["rooms"] and item.get("status") == STATUS_NOT_STARTED:
        item["content"] = catalog.default_content()
    item["status_label"] = STATUS_LABELS.get(item.get("status"), "Not started")
    item["updated_display"] = format_perth(item.get("updated_at"))
    item["submitted_display"] = format_perth(item.get("submitted_at"))
    item["estimated_m3"] = float(item.get("estimated_m3") or 0)
    return item


def normalize_public_id(value: Any) -> str:
    match = PUBLIC_ID_RE.search(str(value or "").strip())
    return "FL-{0}".format(match.group(1).upper()) if match else ""


def _new_public_id(conn) -> str:
    for _ in range(50):
        candidate = "FL-" + "".join(secrets.choice(_PUBLIC_ALPHABET) for _ in range(6))
        if not conn.execute(
            "SELECT 1 FROM furniture_lists WHERE public_id = ?", (candidate,)
        ).fetchone():
            return candidate
    raise RuntimeError("Could not allocate a furniture list id")


def safe_return_url(value: Any) -> str:
    """Only allow https links back to the company website (no open redirects)."""
    text = str(value or "").strip()
    if not text or len(text) > 500:
        return ""
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in RETURN_HOSTS:
        return ""
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k != "furniture_list"]
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def return_link(row: Dict[str, Any]) -> str:
    base = safe_return_url(row.get("return_url"))
    if not base:
        return ""
    parsed = urlparse(base)
    query = parse_qsl(parsed.query) + [("furniture_list", row["public_id"])]
    return urlunparse(parsed._replace(query=urlencode(query), fragment="quote"))


# ---------------------------------------------------------------- lookups

def get_by_token(token: Any) -> Optional[Dict[str, Any]]:
    text = str(token or "")
    if not _TOKEN_RE.match(text):
        return None
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT {0} FROM furniture_lists WHERE access_token = ?".format(_LIST_COLUMNS),
            (text,),
        ).fetchone()
    found = _row_dict(row)
    # Constant-time confirm (the SQL match already did the lookup).
    if found and not secrets.compare_digest(found["access_token"], text):
        return None
    return found


def get_by_id(list_id: Any) -> Optional[Dict[str, Any]]:
    try:
        lid = int(list_id)
    except (TypeError, ValueError):
        return None
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT {0} FROM furniture_lists WHERE id = ?".format(_LIST_COLUMNS), (lid,)
        ).fetchone()
    return _row_dict(row)


def get_by_public_id(value: Any) -> Optional[Dict[str, Any]]:
    public_id = normalize_public_id(value)
    if not public_id:
        return None
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT {0} FROM furniture_lists WHERE public_id = ?".format(_LIST_COLUMNS),
            (public_id,),
        ).fetchone()
    return _row_dict(row)


def get_for_booking(booking_id: Any) -> Optional[Dict[str, Any]]:
    try:
        bid = int(booking_id)
    except (TypeError, ValueError):
        return None
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT {0} FROM furniture_lists WHERE booking_id = ? ORDER BY id LIMIT 1".format(
                _LIST_COLUMNS
            ),
            (bid,),
        ).fetchone()
    return _row_dict(row)


def map_for_bookings(booking_ids: Iterable[Any]) -> Dict[int, Dict[str, Any]]:
    """booking_id → light summary (no content) for many bookings in one query."""
    ids = sorted({int(b) for b in booking_ids if str(b or "").strip().isdigit()})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, public_id, booking_id, status, estimated_m3, item_count, photo_count, "
            "updated_at FROM furniture_lists WHERE booking_id IN ({0}) ORDER BY id".format(
                placeholders
            ),
            tuple(ids),
        ).fetchall()
    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        bid = int(item["booking_id"])
        if bid in out:
            continue
        item["status_label"] = STATUS_LABELS.get(item.get("status"), "Not started")
        out[bid] = item
    return out


def list_recent(limit: int = 200) -> List[Dict[str, Any]]:
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT f.id, f.public_id, f.booking_id, f.source, f.status, f.contact_name, "
            "f.contact_email, f.contact_phone, f.contact_move_date, f.estimated_m3, "
            "f.item_count, f.photo_count, f.created_at, f.updated_at, f.submitted_at, "
            "b.customer_name AS booking_customer, b.move_date AS booking_move_date "
            "FROM furniture_lists f LEFT JOIN bookings b ON b.id = f.booking_id "
            "ORDER BY f.updated_at DESC, f.id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["status_label"] = STATUS_LABELS.get(item.get("status"), "Not started")
        item["updated_display"] = format_perth(item.get("updated_at"))
        out.append(item)
    return out


# ---------------------------------------------------------------- writes

def create_list(
    source: str = SOURCE_BOOKING,
    booking_id: Optional[int] = None,
    return_url: str = "",
    created_ip: str = "",
) -> Dict[str, Any]:
    now = now_iso()
    content = json.dumps(catalog.default_content())
    with db.get_connection() as conn:
        public_id = _new_public_id(conn)
        token = secrets.token_urlsafe(32)
        cursor = conn.execute(
            "INSERT INTO furniture_lists (public_id, access_token, booking_id, source, status, "
            "return_url, content_json, created_at, updated_at, created_ip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                public_id,
                token,
                booking_id,
                source,
                STATUS_NOT_STARTED,
                safe_return_url(return_url),
                content,
                now,
                now,
                (created_ip or "")[:64],
            ),
        )
        list_id = cursor.lastrowid
        conn.commit()
    return get_by_id(list_id)


def website_lists_created_recently(ip_address: str) -> int:
    cutoff = datetime.now(timezone.utc).timestamp() - 3600
    cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM furniture_lists WHERE created_ip = ? AND created_at >= ?",
            ((ip_address or "")[:64], cutoff_iso),
        ).fetchone()
    return int(dict(row)["c"]) if row else 0


def ensure_for_booking(booking_id: int) -> Dict[str, Any]:
    existing = get_for_booking(booking_id)
    if existing:
        return existing
    return create_list(SOURCE_BOOKING, booking_id=int(booking_id))


def _clean_contact(raw: Dict[str, Any]) -> Dict[str, str]:
    def text(key: str, limit: int) -> str:
        return " ".join(str(raw.get(key) or "").split())[:limit]

    move_date = text("move_date", 10)
    if move_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", move_date):
        move_date = ""
    return {
        "contact_name": text("name", 120),
        "contact_email": text("email", 160),
        "contact_phone": text("phone", 40),
        "contact_move_date": move_date,
    }


def save_content(
    row: Dict[str, Any],
    payload: Dict[str, Any],
    editor: str,
    submit: bool = False,
) -> Dict[str, Any]:
    content = catalog.sanitize_content(payload.get("content") if isinstance(payload, dict) else {})
    summary = catalog.summarize(content)
    contact = _clean_contact(payload.get("contact") or {}) if isinstance(payload, dict) else {}
    status = row.get("status") or STATUS_NOT_STARTED
    if submit:
        status = STATUS_SUBMITTED
    elif status == STATUS_NOT_STARTED and (
        catalog.has_content(content) or any(contact.values())
    ):
        status = STATUS_IN_PROGRESS
    now = now_iso()
    submitted_at = now if submit else (row.get("submitted_at") or "")
    fields = {
        "content_json": json.dumps(content, ensure_ascii=False),
        "estimated_m3": summary["estimated_m3"],
        "item_count": summary["item_count"],
        "status": status,
        "updated_at": now,
        "submitted_at": submitted_at,
        "last_edited_by": editor[:40],
    }
    if contact and row.get("source") == SOURCE_WEBSITE:
        fields.update(contact)
    assignments = ", ".join("{0} = ?".format(k) for k in fields)
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE furniture_lists SET {0} WHERE id = ?".format(assignments),
            tuple(fields.values()) + (int(row["id"]),),
        )
        conn.commit()
    return get_by_id(row["id"])


def set_status(row: Dict[str, Any], status: str, editor: str) -> Dict[str, Any]:
    if status not in STATUS_LABELS:
        raise FurnitureError("Unknown status.")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE furniture_lists SET status = ?, updated_at = ?, last_edited_by = ? WHERE id = ?",
            (status, now_iso(), editor[:40], int(row["id"])),
        )
        conn.commit()
    return get_by_id(row["id"])


def link_to_booking(list_id: int, booking_id: int) -> Dict[str, Any]:
    row = get_by_id(list_id)
    if not row:
        raise FurnitureError("Furniture list not found.")
    if not db.get_booking(booking_id):
        raise FurnitureError("Booking #{0} not found.".format(booking_id))
    existing = get_for_booking(booking_id)
    if existing and int(existing["id"]) != int(row["id"]):
        raise FurnitureError(
            "Booking #{0} already has furniture list {1}. Unlink it first.".format(
                booking_id, existing["public_id"]
            )
        )
    if row.get("booking_id") and int(row["booking_id"]) != int(booking_id):
        raise FurnitureError(
            "{0} is already linked to booking #{1}.".format(row["public_id"], row["booking_id"])
        )
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE furniture_lists SET booking_id = ? WHERE id = ?",
            (int(booking_id), int(row["id"])),
        )
        conn.commit()
    return get_by_id(row["id"])


def unlink(list_id: int) -> None:
    with db.get_connection() as conn:
        conn.execute("UPDATE furniture_lists SET booking_id = NULL WHERE id = ?", (int(list_id),))
        conn.commit()


def delete_list(list_id: int) -> None:
    with db.get_connection() as conn:
        conn.execute("DELETE FROM furniture_list_photos WHERE list_id = ?", (int(list_id),))
        conn.execute("DELETE FROM furniture_lists WHERE id = ?", (int(list_id),))
        conn.commit()


def auto_link_from_booking_text(booking: Any) -> Optional[Dict[str, Any]]:
    """Link an unlinked list whose FL id appears in the booking notes (website enquiries)."""
    data = dict(booking)
    if get_for_booking(data["id"]):
        return None
    public_id = normalize_public_id(data.get("notes"))
    if not public_id:
        return None
    row = get_by_public_id(public_id)
    if not row or row.get("booking_id"):
        return None
    try:
        return link_to_booking(row["id"], int(data["id"]))
    except FurnitureError:
        return None


# ---------------------------------------------------------------- photos

def _refresh_photo_count(conn, list_id: int) -> None:
    count_row = conn.execute(
        "SELECT COUNT(*) AS c FROM furniture_list_photos WHERE list_id = ?", (list_id,)
    ).fetchone()
    conn.execute(
        "UPDATE furniture_lists SET photo_count = ?, updated_at = ? WHERE id = ?",
        (int(dict(count_row)["c"]), now_iso(), list_id),
    )


def process_image(raw: bytes) -> Tuple[bytes, int, int]:
    """Validate and re-encode as JPEG (strips EXIF/GPS, fixes rotation, max 2000px)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not raw:
        raise FurnitureError("The photo is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise FurnitureError("Photos must be 15 MB or smaller.")
    Image.MAX_IMAGE_PIXELS = 60_000_000
    try:
        probe = Image.open(io.BytesIO(raw))
        image_format = (probe.format or "").upper()
        probe.verify()
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise FurnitureError("Please upload a JPG, PNG or WebP photo.")
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise FurnitureError("Please upload a JPG, PNG or WebP photo.")
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    elif image.mode == "L":
        image = image.convert("RGB")
    image.thumbnail((PHOTO_MAX_EDGE, PHOTO_MAX_EDGE))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=PHOTO_JPEG_QUALITY, optimize=True)
    return out.getvalue(), image.width, image.height


def add_photo(row: Dict[str, Any], raw: bytes, uploaded_by: str, caption: str = "") -> Dict[str, Any]:
    list_id = int(row["id"])
    with db.get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM furniture_list_photos WHERE list_id = ?", (list_id,)
        ).fetchone()
        if int(dict(count)["c"]) >= MAX_PHOTOS:
            raise FurnitureError("You can add up to {0} photos.".format(MAX_PHOTOS))
    data, width, height = process_image(raw)
    with db.get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO furniture_list_photos (list_id, content_type, data, size_bytes, width, "
            "height, caption, uploaded_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                list_id,
                "image/jpeg",
                db_binary(data),
                len(data),
                width,
                height,
                " ".join(str(caption or "").split())[:200],
                uploaded_by[:40],
                now_iso(),
            ),
        )
        photo_id = cursor.lastrowid
        if row.get("status") == STATUS_NOT_STARTED:
            conn.execute(
                "UPDATE furniture_lists SET status = ? WHERE id = ?",
                (STATUS_IN_PROGRESS, list_id),
            )
        _refresh_photo_count(conn, list_id)
        conn.commit()
    return {"id": int(photo_id), "width": width, "height": height, "size_bytes": len(data)}


def db_binary(data: bytes) -> Any:
    from db_backend import is_postgres

    if is_postgres():
        import psycopg2

        return psycopg2.Binary(data)
    return data


def list_photos(list_id: int) -> List[Dict[str, Any]]:
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, size_bytes, width, height, caption, uploaded_by, created_at "
            "FROM furniture_list_photos WHERE list_id = ? ORDER BY id",
            (int(list_id),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_photo(list_id: int, photo_id: int) -> Optional[Dict[str, Any]]:
    """Photo bytes, only when it belongs to ``list_id`` (enforces per-list access)."""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT id, list_id, content_type, data FROM furniture_list_photos "
            "WHERE id = ? AND list_id = ?",
            (int(photo_id), int(list_id)),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["data"] = bytes(item["data"])
    return item


def delete_photo(list_id: int, photo_id: int) -> bool:
    with db.get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM furniture_list_photos WHERE id = ? AND list_id = ?",
            (int(photo_id), int(list_id)),
        )
        deleted = (cursor.rowcount or 0) > 0
        _refresh_photo_count(conn, int(list_id))
        conn.commit()
    return deleted


# ---------------------------------------------------------------- staff links

def _staff_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.SECRET_KEY, salt="furniture-staff-v1")


def staff_link_token(booking_id: int) -> str:
    return _staff_serializer().dumps({"b": int(booking_id)})


def booking_id_from_staff_token(token: Any) -> Optional[int]:
    try:
        data = _staff_serializer().loads(str(token or ""), max_age=STAFF_LINK_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    try:
        return int(data.get("b"))
    except (AttributeError, TypeError, ValueError):
        return None


def attach_staff_links(jobs: List[Dict[str, Any]]) -> None:
    """Add furniture_token / furniture_status to Staff Portal jobs that have a list."""
    lists = map_for_bookings(job.get("id") for job in jobs)
    for job in jobs:
        found = lists.get(int(job.get("id") or 0))
        if not found or found.get("status") == STATUS_NOT_STARTED:
            continue
        job["furniture_token"] = staff_link_token(int(job["id"]))
        job["furniture_status"] = found["status_label"]
        job["furniture_m3"] = float(found.get("estimated_m3") or 0)


# ---------------------------------------------------------------- view model

def editor_state(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "public_id": row["public_id"],
        "status": row["status"],
        "status_label": row["status_label"],
        "updated_display": row["updated_display"],
        "source": row["source"],
        "content": row["content"],
        "contact": {
            "name": row.get("contact_name") or "",
            "email": row.get("contact_email") or "",
            "phone": row.get("contact_phone") or "",
            "move_date": row.get("contact_move_date") or "",
        },
        "photos": list_photos(row["id"]),
        "estimated_m3": row["estimated_m3"],
    }
