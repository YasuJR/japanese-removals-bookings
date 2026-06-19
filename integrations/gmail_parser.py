"""Extract booking fields from inbound email messages."""

import base64
import re
from datetime import date, datetime, timedelta
from email.utils import parseaddr
from typing import Any, Dict, List, Optional

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
    re.IGNORECASE,
)
PHONE_RE = re.compile(
    r"(?:\+?61|0)[\s\-()]*(?:4\d{2}|[2378]\d)[\s\-()]*\d{3}[\s\-()]*\d{3}"
    r"|\b04\d{2}[\s\-]?\d{3}[\s\-]?\d{3}\b"
)
DATE_PATTERNS = [
    ("%d/%m/%Y", re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")),
    ("%d-%m-%Y", re.compile(r"\b(\d{1,2}-\d{1,2}-\d{2,4})\b")),
    (
        "%Y-%m-%d",
        re.compile(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b"),
    ),
    (
        "%d %B %Y",
        re.compile(
            r"\b(\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
            r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
            r"Nov(?:ember)?|Dec(?:ember)?)\s+20\d{2})\b",
            re.IGNORECASE,
        ),
    ),
]

FIELD_LABELS = {
    "customer_name": (
        r"(?:customer\s*name|name|client|contact)\s*[:\-]\s*(.+)",
    ),
    "phone": (
        r"(?:phone|mobile|tel(?:ephone)?|contact\s*number)\s*[:\-]\s*(.+)",
    ),
    "email": (
        r"(?:email|e-mail)\s*[:\-]\s*(.+)",
    ),
    "move_date": (
        r"(?:move\s*date|moving\s*date|date\s*of\s*move|preferred\s*date|date)\s*[:\-]\s*(.+)",
    ),
    "pickup_address": (
        r"(?:pickup(?:\s*address)?|from|collect(?:ing)?\s*from|pick\s*up)\s*[:\-]\s*(.+)",
    ),
    "delivery_address": (
        r"(?:delivery(?:\s*address)?|to|deliver(?:y)?\s*to|drop[\s\-]?off|destination)\s*[:\-]\s*(.+)",
    ),
    "notes": (
        r"(?:notes|comments|additional\s*info(?:rmation)?|message|details)\s*[:\-]\s*(.+)",
    ),
    "bedrooms": (
        r"(?:bedrooms?|beds?|bed\s*count)\s*[:\-]\s*(.+)",
    ),
    "stairs": (
        r"(?:stairs?|steps?|flights?)\s*[:\-]\s*(.+)",
    ),
    "estimated_volume": (
        r"(?:estimated\s*volume|est(?:imated)?\s*vol(?:ume)?|volume|cbm)\s*[:\-]\s*(.+)",
    ),
    "packing_required": (
        r"(?:packing(?:\s*required)?|need(?:s)?\s*packing|packing\s*service)\s*[:\-]\s*(.+)",
    ),
    "piano": (
        r"(?:piano)\s*[:\-]\s*(.+)",
    ),
    "pool_table": (
        r"(?:pool\s*table|billiard(?:s)?\s*table)\s*[:\-]\s*(.+)",
    ),
}

BEDROOM_INLINE_RE = re.compile(
    r"\b(\d+)\s*[-\s]?(?:bed(?:room)?s?|br)\b",
    re.IGNORECASE,
)
VOLUME_INLINE_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:m3|m³|cbm|cubic\s*met(?:re|er)s?)\b",
    re.IGNORECASE,
)
YES_NO_RE = re.compile(
    r"\b(yes|no|y|n|true|false|required|not\s+required|none|included|needed|na|n/a)\b",
    re.IGNORECASE,
)
NEGATION_RE = re.compile(
    r"\b(?:no|not|without|none|don't|do\s+not|doesn't|does\s+not|never)\b",
    re.IGNORECASE,
)

ITEM_KEYWORDS = {
    "piano": (
        re.compile(r"\b(?:upright\s+)?piano(?:s)?\b", re.IGNORECASE),
        re.compile(r"\b(?:grand|upright)\s+piano\b", re.IGNORECASE),
    ),
    "pool_table": (
        re.compile(r"\bpool\s*table\b", re.IGNORECASE),
        re.compile(r"\bbilliard(?:s)?\s*table\b", re.IGNORECASE),
    ),
    "packing_required": (
        re.compile(r"\b(?:full\s+)?packing(?:\s+service)?\b", re.IGNORECASE),
        re.compile(r"\bpack(?:ing)?\s+required\b", re.IGNORECASE),
        re.compile(r"\bneed(?:s)?\s+packing\b", re.IGNORECASE),
    ),
}

MOVE_DETAIL_KEYS = (
    "bedrooms",
    "stairs",
    "piano",
    "pool_table",
    "packing_required",
    "estimated_volume",
)


def _decode_body_data(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
    except (ValueError, UnicodeEncodeError):
        return ""
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _collect_parts(payload: Dict[str, Any]) -> List[str]:
    texts: List[str] = []
    if not payload:
        return texts

    mime = (payload.get("mimeType") or "").lower()
    body = payload.get("body") or {}
    data = body.get("data")
    if data and mime in ("text/plain", "text/html"):
        texts.append(_decode_body_data(data))

    for part in payload.get("parts") or []:
        texts.extend(_collect_parts(part))
    return texts


def _header_value(headers: List[Dict[str, str]], name: str) -> str:
    target = name.lower()
    for header in headers or []:
        if (header.get("name") or "").lower() == target:
            return (header.get("value") or "").strip()
    return ""


def _plain_text_from_message(message: Dict[str, Any]) -> str:
    payload = message.get("payload") or {}
    chunks = _collect_parts(payload)
    if not chunks:
        body = (payload.get("body") or {}).get("data")
        if body:
            chunks.append(_decode_body_data(body))
    text = "\n".join(chunks)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\r\n?", "\n", text)
    return text.strip()


def _first_match(patterns: tuple, text: str, flags: int = re.IGNORECASE) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            value = (match.group(1) or "").strip()
            value = re.split(r"[\n\r]", value)[0].strip()
            if value:
                return value
    return ""


def _normalize_phone(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    match = PHONE_RE.search(text)
    if match:
        return re.sub(r"\s+", " ", match.group(0).strip())
    digits = re.sub(r"[^\d+]", "", text)
    return digits if len(digits) >= 8 else ""


def _normalize_email(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    match = EMAIL_RE.search(text)
    return match.group(0).strip().lower() if match else ""


def _parse_move_date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    for fmt, pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(1)
        try:
            if fmt == "%d/%m/%Y" and len(raw.split("/")[-1]) == 2:
                parsed = datetime.strptime(raw, "%d/%m/%y").date()
            elif fmt == "%d-%m-%Y" and len(raw.split("-")[-1]) == 2:
                parsed = datetime.strptime(raw, "%d-%m-%y").date()
            else:
                parsed = datetime.strptime(raw, fmt).date()
            return parsed.isoformat()
        except ValueError:
            continue
    return ""


def _sender_details(headers: List[Dict[str, str]]) -> Dict[str, str]:
    from_header = _header_value(headers, "From")
    name, email = parseaddr(from_header)
    return {
        "customer_name": (name or "").strip(),
        "email": (email or "").strip().lower(),
    }


def _normalize_bedrooms(value: str, text: str) -> str:
    labeled = (value or "").strip()
    if labeled:
        match = re.search(r"\b(\d+)\b", labeled)
        if match:
            count = int(match.group(1))
            return "{0} bedroom{1}".format(count, "" if count == 1 else "s")
        lowered = labeled.lower()
        if "studio" in lowered:
            return "Studio"
        return labeled.splitlines()[0].strip()

    match = BEDROOM_INLINE_RE.search(text)
    if match:
        count = int(match.group(1))
        return "{0} bedroom{1}".format(count, "" if count == 1 else "s")
    if re.search(r"\bstudio\b", text, re.IGNORECASE):
        return "Studio"
    return ""


def _normalize_volume(value: str, text: str) -> str:
    labeled = (value or "").strip()
    source = labeled or text
    match = VOLUME_INLINE_RE.search(source)
    if match:
        return "{0} m³".format(match.group(1))
    if labeled:
        return labeled.splitlines()[0].strip()
    return ""


def _normalize_yes_no(value: str, text: str, keywords: tuple) -> str:
    labeled = (value or "").strip()
    if labeled:
        lowered = labeled.lower()
        if NEGATION_RE.search(lowered) and not re.search(
            r"\b(?:yes|required|needed)\b", lowered
        ):
            return "No"
        yes_no = YES_NO_RE.search(labeled)
        if yes_no:
            token = yes_no.group(1).lower()
            if token in ("no", "n", "false", "not required", "none", "na", "n/a"):
                return "No"
            if len(labeled) > len(yes_no.group(0)):
                return labeled.splitlines()[0].strip()
            return "Yes"
        return labeled.splitlines()[0].strip()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for pattern in keywords:
            match = pattern.search(line)
            if not match:
                continue
            if NEGATION_RE.search(line) and not re.search(
                r"\b(?:yes|required|needed)\b", line, re.IGNORECASE
            ):
                return "No"
            detail = match.group(0).strip()
            if detail.lower() in ("packing", "packing service", "full packing"):
                return "Yes"
            return "Yes ({0})".format(detail)
    return ""


def _normalize_stairs(value: str, text: str) -> str:
    labeled = (value or "").strip()
    if labeled:
        lowered = labeled.lower()
        if re.search(r"\b(?:no|none|ground\s*floor|lift|elevator|single\s*level)\b", lowered):
            if "stair" in lowered or "flight" in lowered or "step" in lowered:
                return labeled.splitlines()[0].strip()
            return labeled.splitlines()[0].strip()
        return labeled.splitlines()[0].strip()

    if re.search(r"\b(?:no\s+stairs?|ground\s+floor|single\s+level)\b", text, re.IGNORECASE):
        return "No stairs"
    flight = re.search(
        r"\b(\d+)\s*(?:flights?|levels?)\s*of\s*stairs?\b",
        text,
        re.IGNORECASE,
    )
    if flight:
        return "{0} flights of stairs".format(flight.group(1))
    if re.search(r"\bstairs?\b", text, re.IGNORECASE):
        return "Mentioned — see email body"
    return ""


def _extract_move_details(combined: str, fields: Dict[str, str]) -> Dict[str, str]:
    details = {
        "bedrooms": _normalize_bedrooms(fields.get("bedrooms") or "", combined),
        "stairs": _normalize_stairs(fields.get("stairs") or "", combined),
        "piano": _normalize_yes_no(
            fields.get("piano") or "",
            combined,
            ITEM_KEYWORDS["piano"],
        ),
        "pool_table": _normalize_yes_no(
            fields.get("pool_table") or "",
            combined,
            ITEM_KEYWORDS["pool_table"],
        ),
        "packing_required": _normalize_yes_no(
            fields.get("packing_required") or "",
            combined,
            ITEM_KEYWORDS["packing_required"],
        ),
        "estimated_volume": _normalize_volume(
            fields.get("estimated_volume") or "",
            combined,
        ),
    }
    return details


def format_move_details_section(details: Dict[str, str]) -> str:
    labels = {
        "bedrooms": "Bedrooms",
        "stairs": "Stairs",
        "piano": "Piano",
        "pool_table": "Pool table",
        "packing_required": "Packing required",
        "estimated_volume": "Estimated volume",
    }
    lines = []
    for key in MOVE_DETAIL_KEYS:
        value = (details.get(key) or "").strip()
        if value:
            lines.append("- {0}: {1}".format(labels[key], value))
    if not lines:
        return ""
    return "Move details (from email):\n" + "\n".join(lines)


def build_booking_notes(
    fields: Dict[str, str],
    details: Optional[Dict[str, str]] = None,
) -> str:
    """Combine extracted move details and free-text email notes."""
    move_details = details or _extract_move_details(
        fields.get("source_text") or "",
        fields,
    )
    sections = []

    detail_block = format_move_details_section(move_details)
    if detail_block:
        sections.append(detail_block)

    subject = (fields.get("subject") or "").strip()
    free_notes = (fields.get("notes") or "").strip()
    if subject:
        sections.append("Email subject: {0}".format(subject))
    if free_notes and free_notes != subject:
        sections.append(free_notes)

    return "\n\n".join(section for section in sections if section).strip()


def parse_gmail_message(message: Dict[str, Any]) -> Dict[str, str]:
    """Return extracted booking fields from a Gmail API message resource."""
    headers = (message.get("payload") or {}).get("headers") or []
    subject = _header_value(headers, "Subject")
    body_text = _plain_text_from_message(message)
    combined = "\n".join(part for part in (subject, body_text) if part).strip()

    sender = _sender_details(headers)
    fields = {
        "customer_name": "",
        "phone": "",
        "email": "",
        "move_date": "",
        "pickup_address": "",
        "delivery_address": "",
        "notes": "",
        "bedrooms": "",
        "stairs": "",
        "piano": "",
        "pool_table": "",
        "packing_required": "",
        "estimated_volume": "",
        "subject": subject,
        "source_text": combined[:4000],
    }

    for key, patterns in FIELD_LABELS.items():
        fields[key] = _first_match(patterns, combined)

    if not fields["customer_name"]:
        fields["customer_name"] = sender["customer_name"]
    if not fields["email"]:
        fields["email"] = sender["email"]
    if not fields["phone"]:
        fields["phone"] = _normalize_phone(combined)
    else:
        fields["phone"] = _normalize_phone(fields["phone"])

    fields["email"] = _normalize_email(fields["email"]) or sender["email"]
    fields["move_date"] = _parse_move_date(fields["move_date"] or combined)

    if not fields["notes"] and body_text:
        fields["notes"] = body_text[:2000]

    move_details = _extract_move_details(combined, fields)
    for key in MOVE_DETAIL_KEYS:
        fields[key] = move_details.get(key) or ""

    fields["notes"] = build_booking_notes(fields, move_details)

    return fields


def missing_required_fields(fields: Dict[str, str]) -> List[str]:
    required = (
        "customer_name",
        "pickup_address",
        "delivery_address",
        "move_date",
    )
    missing = []
    for key in required:
        if not (fields.get(key) or "").strip():
            missing.append(key)
    return missing


def default_move_date() -> str:
    return (date.today() + timedelta(days=7)).isoformat()
