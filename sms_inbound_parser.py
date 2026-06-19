"""Phase 21 — parse inbound customer SMS into booking fields."""

import re
from datetime import date, timedelta
from typing import Any, Dict, Tuple

from integrations import gmail_parser

FROM_TO_RE = re.compile(
    r"(?:from|pickup|collect(?:ing)?\s*from)\s+(.+?)\s+(?:to|deliver(?:y)?\s*to|drop[\s-]?off)\s+(.+)",
    re.IGNORECASE,
)
NAME_RE = re.compile(
    r"(?:^|\b)(?:i(?:'m|\s+am)|my\s+name\s+is|this\s+is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    re.IGNORECASE,
)
CONFIDENCE_THRESHOLD = 80.0


def _normalise_phone(from_number: str) -> str:
    text = (from_number or "").strip()
    if text.startswith("+61"):
        return "0{0}".format(text[3:].replace(" ", ""))
    return text.replace(" ", "")


def _suburb_address(text: str) -> str:
    value = (text or "").strip().rstrip(".")
    if not value:
        return ""
    if "wa" in value.lower():
        return value
    return "{0}, WA".format(value)


def _labelled_value(body: str, field_key: str) -> str:
    patterns = gmail_parser.FIELD_LABELS.get(field_key, ())
    return gmail_parser._first_match(patterns, body)


def _first_date_in_text(body: str) -> str:
    for _fmt, pattern in gmail_parser.DATE_PATTERNS:
        match = pattern.search(body or "")
        if match:
            parsed = gmail_parser._parse_move_date(match.group(1))
            if parsed:
                return parsed
    return ""


def _extract_name(body: str) -> str:
    labelled = _labelled_value(body, "customer_name")
    if labelled:
        return labelled.split("\n")[0].strip()
    match = NAME_RE.search(body or "")
    if match:
        return match.group(1).strip()
    return ""


def _extract_move_date(body: str) -> str:
    labelled = _labelled_value(body, "move_date")
    if labelled:
        parsed = gmail_parser._parse_move_date(labelled)
        if parsed:
            return parsed
    return _first_date_in_text(body)


def _extract_locations(body: str) -> Tuple[str, str]:
    pickup = _labelled_value(body, "pickup_address")
    delivery = _labelled_value(body, "delivery_address")
    if pickup or delivery:
        return _suburb_address(pickup), _suburb_address(delivery)

    match = FROM_TO_RE.search(body or "")
    if match:
        return _suburb_address(match.group(1)), _suburb_address(match.group(2))

    return "", ""


def _has_real_address(value: Any) -> bool:
    text = (value or "").strip()
    return bool(text) and not text.lower().startswith("tbc")


def confidence_score(fields: Dict[str, Any]) -> float:
    score = 0.0
    if (fields.get("phone") or "").strip():
        score += 25.0
    name = (fields.get("customer_name") or "").strip()
    if name and name.lower() not in ("sms enquiry", "unknown"):
        score += 15.0
    if (fields.get("move_date") or "").strip():
        score += 25.0
    if _has_real_address(fields.get("pickup_address")):
        score += 17.0
    if _has_real_address(fields.get("delivery_address")):
        score += 18.0
    return min(score, 100.0)


def parse_inbound_sms(from_number: str, body: str) -> Dict[str, Any]:
    """Parse Twilio inbound SMS into lead/booking fields."""
    message = (body or "").strip()
    phone = _normalise_phone(from_number)
    name = _extract_name(message) or "SMS enquiry"
    move_date = _extract_move_date(message)
    pickup, delivery = _extract_locations(message)
    if not move_date:
        move_date = (date.today() + timedelta(days=14)).isoformat()

    notes = message
    if not pickup and not delivery:
        notes = "SMS enquiry — locations not detected.\n\n{0}".format(message)

    fields = {
        "customer_name": name,
        "phone": phone,
        "email": "",
        "move_date": move_date,
        "pickup_address": pickup or "TBC — see SMS notes",
        "delivery_address": delivery or "TBC — see SMS notes",
        "notes": notes,
        "raw_message": message,
        "source": "SMS",
    }
    fields["confidence"] = confidence_score(fields)
    return fields


def meets_booking_threshold(fields: Dict[str, Any]) -> bool:
    return float(fields.get("confidence") or 0) >= CONFIDENCE_THRESHOLD
