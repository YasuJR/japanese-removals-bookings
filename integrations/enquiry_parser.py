"""Parse pasted customer enquiry text into booking form fields."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from integrations import gmail_parser
import sms_inbound_parser

NAME_PATTERNS = (
    re.compile(
        r"(?:^|\b)(?:i(?:'m|\s+am)|my\s+name\s+is|this\s+is)\s+"
        r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\b)(?:i(?:'m|\s+am)|my\s+name\s+is|this\s+is)\s+"
        r"([A-Z][A-Za-z]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:customer\s*name|name|client|contact)\s*[:\-]\s*(.+)",
        re.IGNORECASE,
    ),
)
MOVING_TO_RE = re.compile(
    r"(?:moving\s+to|relocate(?:ing)?\s+to|deliver(?:y)?\s+to|drop[\s-]?off\s+at)\s+(.+?)"
    r"(?:\n|$|\.\.\.|\.(?!\d))",
    re.IGNORECASE,
)
START_TIME_PATTERNS = (
    re.compile(
        r"(?:start(?:\s*time)?|starting\s+at|arrive|arrival)\s*[:\-]?\s*"
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm))",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
        re.IGNORECASE,
    ),
)


def _pick(*values: str) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


def _extract_name(text: str) -> str:
    for pattern in NAME_PATTERNS:
        match = pattern.search(text or "")
        if match:
            name = match.group(1).strip().splitlines()[0].strip(" ,.-")
            if name and name.lower() not in ("sms enquiry", "unknown"):
                return name
    labelled = gmail_parser._first_match(
        gmail_parser.FIELD_LABELS.get("customer_name", ()),
        text,
    )
    return labelled.splitlines()[0].strip() if labelled else ""


def _extract_phone(text: str) -> str:
    labelled = gmail_parser._first_match(
        gmail_parser.FIELD_LABELS.get("phone", ()),
        text,
    )
    return gmail_parser._normalize_phone(labelled or text)


def _extract_email(text: str) -> str:
    labelled = gmail_parser._first_match(
        gmail_parser.FIELD_LABELS.get("email", ()),
        text,
    )
    return gmail_parser._normalize_email(labelled or text)


def _extract_move_date(text: str) -> str:
    labelled = gmail_parser._first_match(
        gmail_parser.FIELD_LABELS.get("move_date", ()),
        text,
    )
    parsed = gmail_parser._parse_move_date(labelled or text)
    return parsed or gmail_parser.default_move_date()


def _extract_locations(text: str) -> Tuple[str, str]:
    pickup = gmail_parser._first_match(
        gmail_parser.FIELD_LABELS.get("pickup_address", ()),
        text,
    )
    delivery = gmail_parser._first_match(
        gmail_parser.FIELD_LABELS.get("delivery_address", ()),
        text,
    )
    if pickup or delivery:
        return pickup.strip(), delivery.strip()

    sms_pickup, sms_delivery = sms_inbound_parser._extract_locations(text)
    if sms_pickup or sms_delivery:
        return sms_pickup, sms_delivery

    moving_match = MOVING_TO_RE.search(text or "")
    if moving_match:
        delivery = moving_match.group(1).strip().rstrip(".")
        before = (text or "")[: moving_match.start()].strip().splitlines()
        pickup = ""
        for line in reversed(before):
            line = line.strip()
            if not line:
                continue
            if re.search(r"\b(?:this\s+is|hi\s+|hello\b)", line, re.IGNORECASE):
                continue
            if gmail_parser.PHONE_RE.search(line):
                continue
            if gmail_parser._parse_move_date(line):
                continue
            pickup = line.rstrip(".")
            break
        return pickup, delivery

    return "", ""


def _parse_start_time_24h(value: str) -> str:
    text = (value or "").strip().lower().replace(".", "")
    if not text:
        return ""
    for fmt in ("%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%H:%M")
        except ValueError:
            continue
    return ""


def _extract_start_time(text: str) -> str:
    for pattern in START_TIME_PATTERNS:
        match = pattern.search(text or "")
        if match:
            parsed = _parse_start_time_24h(match.group(1))
            if parsed:
                return parsed
    return ""


def _line_matches_field(line: str, value: str) -> bool:
    text = (line or "").strip()
    field = (value or "").strip()
    if not text or not field:
        return False
    if field.lower() in text.lower():
        return True
    if field.replace(" ", "") in text.replace(" ", ""):
        return True
    return False


def _extract_notes(text: str, parsed: Dict[str, Any]) -> str:
    """Keep only supplementary lines not already mapped to structured fields."""
    remaining = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _line_matches_field(stripped, parsed.get("customer_name", "")):
            continue
        if gmail_parser.PHONE_RE.search(stripped) or _line_matches_field(
            stripped, parsed.get("phone", "")
        ):
            continue
        if _line_matches_field(stripped, parsed.get("pickup_address", "")):
            continue
        if _line_matches_field(stripped, parsed.get("delivery_address", "")):
            continue
        if MOVING_TO_RE.search(stripped):
            continue
        if gmail_parser._parse_move_date(stripped):
            continue
        if _extract_start_time(stripped):
            continue
        if re.search(
            r"\b(?:start(?:\s*time)?|starting\s+at|arrive|arrival)\b",
            stripped,
            re.IGNORECASE,
        ):
            continue
        remaining.append(stripped)
    return "\n".join(remaining).strip()


def _build_notes(text: str, fields: Dict[str, str]) -> str:
    parsed = {
        "customer_name": fields.get("customer_name") or "",
        "phone": fields.get("phone") or "",
        "pickup_address": fields.get("pickup_address") or "",
        "delivery_address": fields.get("delivery_address") or "",
    }
    supplementary = _extract_notes(text, parsed)
    if supplementary:
        return supplementary

    gmail_fields = dict(fields)
    gmail_fields["source_text"] = text[:4000]
    gmail_fields["subject"] = ""
    move_details = gmail_parser._extract_move_details(text, gmail_fields)
    for key in gmail_parser.MOVE_DETAIL_KEYS:
        gmail_fields[key] = move_details.get(key) or ""
    notes = gmail_parser.build_booking_notes(gmail_fields, move_details)
    if notes:
        return notes
    return text.strip()[:2000]


def parse_pasted_text(text: str) -> Dict[str, Any]:
    """Parse free-form pasted enquiry text into booking fields."""
    message = (text or "").strip()
    empty = {
        "customer_name": "",
        "phone": "",
        "email": "",
        "move_date": "",
        "pickup_address": "",
        "delivery_address": "",
        "start_time": "",
        "notes": "",
        "source_text": message,
    }
    if not message:
        empty["confidence"] = 0.0
        return empty

    sms_fields = sms_inbound_parser.parse_inbound_sms("", message)
    customer_name = _pick(_extract_name(message), sms_fields.get("customer_name", ""))
    phone = _pick(_extract_phone(message), sms_fields.get("phone", ""))
    email = _extract_email(message)
    move_date = _pick(_extract_move_date(message), sms_fields.get("move_date", ""))
    pickup, delivery = _extract_locations(message)
    pickup = _pick(pickup, sms_fields.get("pickup_address", ""))
    delivery = _pick(delivery, sms_fields.get("delivery_address", ""))
    start_time = _extract_start_time(message)

    fields = {
        "customer_name": customer_name,
        "phone": phone,
        "email": email,
        "move_date": move_date,
        "pickup_address": pickup,
        "delivery_address": delivery,
        "start_time": start_time,
        "notes": _build_notes(message, {
            "customer_name": customer_name,
            "phone": phone,
            "email": email,
            "move_date": move_date,
            "pickup_address": pickup,
            "delivery_address": delivery,
            "notes": message,
        }),
        "source_text": message,
    }
    fields["confidence"] = sms_inbound_parser.confidence_score(fields)
    return fields


def apply_parsed_fields(form: Dict[str, Any], parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Merge parsed values into a booking form dict."""
    merged = dict(form)
    mapping = {
        "customer_name": parsed.get("customer_name") or "",
        "phone": parsed.get("phone") or "",
        "email": parsed.get("email") or "",
        "move_date": parsed.get("move_date") or "",
        "pickup_address": parsed.get("pickup_address") or "",
        "delivery_address": parsed.get("delivery_address") or "",
        "notes": parsed.get("notes") or "",
        "start_time": parsed.get("start_time") or merged.get("start_time", ""),
    }
    merged.update(mapping)
    return merged


def format_start_time_display(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "—"
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            hour = parsed.strftime("%I").lstrip("0") or "12"
            return "{0}:{1} {2}".format(hour, parsed.strftime("%M"), parsed.strftime("%p"))
        except ValueError:
            continue
    return text


def summary_rows(parsed: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Human-readable rows for the analyse preview panel."""
    return [
        ("Name", (parsed.get("customer_name") or "").strip() or "—"),
        ("Phone", (parsed.get("phone") or "").strip() or "—"),
        ("From", (parsed.get("pickup_address") or "").strip() or "—"),
        ("To", (parsed.get("delivery_address") or "").strip() or "—"),
        ("Date", _format_move_date_display(parsed.get("move_date") or "")),
        (
            "Start Time",
            format_start_time_display(parsed.get("start_time") or ""),
        ),
        ("Notes", _truncate_notes(parsed.get("notes") or "")),
    ]


def _format_move_date_display(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "—"
    try:
        parsed = datetime.strptime(text[:10], "%Y-%m-%d").date()
        return parsed.strftime("%d/%m/%Y")
    except ValueError:
        return text


def _truncate_notes(value: str, limit: int = 120) -> str:
    text = (value or "").strip()
    if not text:
        return "—"
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
