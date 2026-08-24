"""Parse pasted customer enquiry text into booking form fields."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import config
import sms_inbound_parser
from integrations import gmail_parser

NAME_PATTERNS = (
    re.compile(
        r"(?:^|\b)(?:i(?:'m|\s+am)|my\s+name\s+is|this\s+is)\s+"
        r"([A-Za-z]+(?:\s+[A-Za-z]+)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\b)([A-Za-z]+(?:\s+[A-Za-z]+)?)\s+here\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:regards|thanks|thank\s+you|cheers),?\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"(?:customer\s*name|name|client|contact)\s*[:\-]\s*(.+)",
        re.IGNORECASE,
    ),
)
ADDRESS_TAIL_STOP = (
    r"(?:[.!?]|$|\n|\s+sometime|\s+please|\s+phone|\s+mobile|\s+email|"
    r"\s+thanks|\s+regards)"
)
FROM_TO_LINE_RE = re.compile(
    r"\bfrom\s+(.+?)\s+to\s+(.+?)" + ADDRESS_TAIL_STOP,
    re.IGNORECASE,
)
STREET_TO_STREET_RE = re.compile(
    r"\b(\d{1,5}[A-Za-z]?\s+.+?)\s+to\s+(\d{1,5}[A-Za-z]?\s+.+?)" + ADDRESS_TAIL_STOP,
    re.IGNORECASE,
)
MOVING_FROM_TO_RE = re.compile(
    r"\bmoving\s+from\s+(.+?)\s+to\s+(.+?)" + ADDRESS_TAIL_STOP,
    re.IGNORECASE,
)
MOVING_TO_RE = re.compile(
    r"(?:moving\s+to|relocate(?:ing)?\s+to|deliver(?:y)?\s+to|drop[\s-]?off(?:\s+at)?)\s+(.+?)"
    r"(?:\n|$|\.\.\.|\.(?!\d))",
    re.IGNORECASE,
)
PICKUP_LABELS = (
    r"(?:pick[\s-]*up|pickup)(?:\s+address)?\s*(?:is|:|-)\s*(.+)",
    r"(?:pick[\s-]*up|pickup|collect(?:ing)?)\s+from\s+(.+)",
    r"(?:collection(?:\s+address)?|current\s+address)\s*(?:is|:|-)\s*(.+)",
    r"(?:moving\s+from)\s+(.+)",
    r"(?:from)\s*[:\-]\s*(.+)",
)
DELIVERY_LABELS = (
    r"(?:destination|delivery(?:\s+address)?|new\s+place)\s*(?:is|:|-)\s*(.+)",
    r"(?:delivery(?:\s+address)?)\s*[:\-]?\s*(.+)",
    r"(?:deliver(?:y)?\s+to|drop[\s-]?off(?:\s+(?:to|at|is))?|moving\s+to)\s*[:\-]?\s*(.+)",
    r"(?:to)\s*[:\-]\s*(.+)",
)
STREET_NUMBER_START = r"(?:\d{1,5}[A-Za-z]?(?:\s*/\s*\d{1,5})?)"
STREET_ADDRESS_FRAGMENT = (
    STREET_NUMBER_START + r"\s+[A-Za-z][^,\n]*(?:,\s*[^,\n]+)?"
)
UNIT_PREFIX = r"(?:Unit\s+\d+[A-Za-z]?,\s*)"
DELIVERY_ADDRESS_FRAGMENT = (
    UNIT_PREFIX + "?" + STREET_NUMBER_START + r"\s+[A-Za-z][^,\n]*(?:,\s*[^,\n]+)*"
)
DUAL_ADDRESS_BRIDGE = r"\s*(?:and\s+the\s+)?(?:and\s+)?[\s,—–\-]*"
DUAL_ADDRESS_INDICATORS = (
    r"moving\s+to|new\s+address\s+is|delivery\s+address\s+is|destination\s+is|new\s+place\s+is"
)
NEW_ADDRESS_IS_RE = re.compile(
    r"(?P<pickup>{pickup})\s+and\s+the\s+new\s+address\s+is\s*(?:\n\s*)?(?P<delivery>{delivery}[^\n]*)".format(
        pickup=STREET_ADDRESS_FRAGMENT,
        delivery=DELIVERY_ADDRESS_FRAGMENT,
    ),
    re.IGNORECASE,
)
DELIVERY_INDICATOR_RE = re.compile(
    r"(?P<pickup>{pickup}){bridge}(?:{indicators})\s*(?:\n\s*)?(?P<delivery>{delivery}[^\n]*)".format(
        pickup=STREET_ADDRESS_FRAGMENT,
        bridge=DUAL_ADDRESS_BRIDGE,
        indicators=DUAL_ADDRESS_INDICATORS,
        delivery=DELIVERY_ADDRESS_FRAGMENT,
    ),
    re.IGNORECASE,
)
INVALID_ADDRESS_TOKENS = frozenset(
    {"is", "to", "and", "from", "at", "the", "a", "an", "in", "on", "of"}
)
FIRST_NAME_RE = re.compile(r"first\s*name\s*[:\-]\s*(.+)", re.IGNORECASE)
LAST_NAME_RE = re.compile(r"last\s*name\s*[:\-]\s*(.+)", re.IGNORECASE)
LABELLED_FIELD_LINE_RE = re.compile(
    r"^\s*(?:first\s*name|last\s*name|phone\s*number|phone|mobile|email|e-mail)\s*[:\-]",
    re.IGNORECASE,
)
STREET_HINT_RE = re.compile(
    r"\b\d+[A-Za-z]?\s+\w+|"
    r"\b\d+\s*/\s*\d+\b|"
    r"\b(?:st|street|rd|road|ave|avenue|hwy|highway|way|blvd|court|ct|drive|dr|"
    r"place|pl|parade|pde|crescent|cres|terrace|tce|boulevard|lane|ln)\b",
    re.IGNORECASE,
)
SUBURB_ONLY_RE = re.compile(r"^[A-Za-z][A-Za-z\s\-']+$")
TIME_EXACT_RE = re.compile(
    r"(?<!around )(?<!about )\b(?:at\s+|@)?(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
    re.IGNORECASE,
)
TIME_APPROX_RE = re.compile(
    r"\b(?:around|about|approx(?:\.|imately)?)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\d{1,2}(?::\d{2})?)\b",
    re.IGNORECASE,
)
TIME_AFTER_RE = re.compile(
    r"\b(?:after|from)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
    re.IGNORECASE,
)
TIME_START_LABEL_RE = re.compile(
    r"(?:start(?:\s*time)?|starting\s+at|arrive|arrival)\s*[:\-]?\s*"
    r"(\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    re.IGNORECASE,
)
VAGUE_TIME_RE = re.compile(r"\b(?:morning|afternoon|evening|arvo)\b", re.IGNORECASE)
DATE_AMBIGUOUS_RE = re.compile(
    r"\b(?:sometime|some\s+time)\s+(?:next\s+week|this\s+week|later)\b|"
    r"\bnext\s+week\b(?!\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)|"
    r"\b(?:early|mid|late)\s+next\s+week\b",
    re.IGNORECASE,
)
NOTE_KEYWORDS = (
    ("piano", re.compile(r"\bpiano\b", re.IGNORECASE)),
    ("stairs", re.compile(r"\bstairs?\b", re.IGNORECASE)),
    ("lift/elevator", re.compile(r"\b(?:lift|elevator)\b", re.IGNORECASE)),
    ("packing", re.compile(r"\b(?:packing|need\s+packing|pack(?:ing)?\s+help)\b", re.IGNORECASE)),
    ("boxes", re.compile(r"\bbox(?:es)?\b", re.IGNORECASE)),
    ("dolly", re.compile(r"\bdolly\b", re.IGNORECASE)),
    ("apartment/unit", re.compile(r"\b(?:apartment|unit)\b", re.IGNORECASE)),
    ("parking restrictions", re.compile(r"\bparking\b", re.IGNORECASE)),
    ("access issues", re.compile(r"\b(?:access|narrow\s+driveway|no\s+parking)\b", re.IGNORECASE)),
    ("dismantling/reassembly", re.compile(r"\b(?:dismantl|re-?assembly|reassembly)\b", re.IGNORECASE)),
    ("storage", re.compile(r"\bstorage\b", re.IGNORECASE)),
    ("fragile items", re.compile(r"\b(?:fragile|antique|glassware)\b", re.IGNORECASE)),
)
WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
STANDALONE_TO_RE = re.compile(
    r"(?:^|[.\n])\s*to\s+(\d+[A-Za-z]?\s+.+?)(?:[.!?]|$|\n)",
    re.IGNORECASE | re.MULTILINE,
)
_WEEKDAY_PATTERN = "|".join(WEEKDAY_NAMES.keys())
_MONTH_PATTERN = "|".join(MONTH_NAMES.keys())
SCHEDULE_TAIL_START_RE = re.compile(
    r"\s+(?:"
    r"next\s+(?:week|{0})|"
    r"this\s+(?:{0})|"
    r"tomorrow|"
    r"on\s+(?:\d{{1,2}}\s+(?:{1})|\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?|(?:{1})\s+\d{{1,2}})|"
    r"(?:on\s+)?(?:{0})|"
    r"\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?|"
    r"(?:at|around|about|after)\s+\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm)?|"
    r"(?:at|around|about|after)\s+\d{{1,2}}(?::\d{{2}})?|"
    r"morning|afternoon|evening|arvo"
    r")\b".format(_WEEKDAY_PATTERN, _MONTH_PATTERN),
    re.IGNORECASE,
)
INVALID_NAMES = {
    "sms enquiry",
    "unknown",
    "hi",
    "hello",
    "hey",
    "hi there",
    "hello there",
    "hey there",
    "thanks",
    "thank you",
    "moving",
    "move",
    "mate",
    "us on",
    "you",
    "us",
}
NAME_STOPWORDS = frozenset(
    {
        "i",
        "me",
        "my",
        "we",
        "us",
        "you",
        "he",
        "she",
        "it",
        "they",
        "them",
        "their",
        "this",
        "that",
        "here",
        "there",
        "on",
        "in",
        "at",
        "to",
        "from",
        "for",
        "of",
        "and",
        "or",
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "am",
        "do",
        "did",
        "please",
        "thanks",
        "thank",
        "hi",
        "hello",
        "hey",
        "moving",
        "move",
        "moved",
        "pickup",
        "pick",
        "up",
        "drop",
        "off",
        "destination",
        "delivery",
        "deliver",
        "address",
        "phone",
        "email",
        "mobile",
        "mate",
        "looking",
        "need",
        "help",
        "with",
        "last",
        "year",
        "sms",
        "enquiry",
    }
)
STREET_TYPE_NORMAL = {
    "st": "St",
    "street": "Street",
    "rd": "Rd",
    "road": "Road",
    "ave": "Ave",
    "avenue": "Avenue",
    "hwy": "Hwy",
    "highway": "Highway",
    "dr": "Dr",
    "drive": "Drive",
    "ct": "Ct",
    "court": "Court",
    "pl": "Pl",
    "place": "Place",
    "pde": "Pde",
    "parade": "Parade",
    "cres": "Cres",
    "crescent": "Crescent",
    "tce": "Tce",
    "terrace": "Terrace",
    "blvd": "Blvd",
    "boulevard": "Boulevard",
    "ln": "Ln",
    "lane": "Lane",
    "way": "Way",
}
STREET_TYPE_RE = re.compile(
    r"\b(" + "|".join(sorted(STREET_TYPE_NORMAL, key=len, reverse=True)) + r")\b\.?",
    re.IGNORECASE,
)
PARENTHETICAL_RE = re.compile(r"\(([^)]*)\)")
ADDRESS_LEAD_RE = re.compile(
    r"^\s*(?:pick[\s-]*up|pickup|destination|deliver(?:y)?|drop[\s-]?off|"
    r"from|to|moving|collection|collect)\b",
    re.IGNORECASE,
)
GREETING_LINE_RE = re.compile(
    r"^\s*(?:hi|hello|hey)(?:\s+there)?[\s,!.-]*$",
    re.IGNORECASE,
)


def _perth_today(reference: Optional[datetime] = None) -> date:
    if reference is not None:
        if reference.tzinfo is None:
            return reference.date()
        return reference.astimezone(ZoneInfo(config.TIMEZONE)).date()
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def _pick(*values: str) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


def _is_plausible_person_name(value: str) -> bool:
    name = _clean_name(value)
    if not name:
        return False
    words = [word.strip(".,") for word in name.split() if word.strip(".,")]
    if not 1 <= len(words) <= 3:
        return False
    if any(word.lower() in NAME_STOPWORDS for word in words):
        return False
    if re.search(r"\d", name):
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2}$", name))


def _clean_name(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    name = text.splitlines()[0].strip(" ,.-")
    if not name:
        return ""
    if name.lower() in INVALID_NAMES:
        return ""
    words = [word.strip(".,") for word in name.split() if word.strip(".,")]
    if any(word.lower() in NAME_STOPWORDS for word in words):
        return ""
    if len(name) < 2:
        return ""
    return name


def _extract_first_last_name(text: str) -> str:
    """Combine labelled First Name and Last Name fields."""
    first_match = FIRST_NAME_RE.search(text or "")
    last_match = LAST_NAME_RE.search(text or "")
    if not first_match and not last_match:
        return ""
    first = _clean_name(first_match.group(1)) if first_match else ""
    last = _clean_name(last_match.group(1)) if last_match else ""
    combined = _clean_name("{0} {1}".format(first, last).strip())
    return combined if _is_plausible_person_name(combined) else ""


def _name_prefix_from_line(line: str) -> str:
    text = (line or "").strip()
    if not text:
        return ""
    text = re.split(
        r"(?:\+?61\d[\d\s\-]*|0\d[\d\s\-]*|"
        r"pick[\s-]*up|pickup|destination|deliver(?:y)?|drop[\s-]?off|"
        r"moving\s+from|phone|mobile|email|"
        r"(?:from|to)\s*:)",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return text.strip(" -,")


def _extract_leading_name(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if GREETING_LINE_RE.match(stripped):
            continue
        if LABELLED_FIELD_LINE_RE.match(stripped):
            break
        prefix = _name_prefix_from_line(stripped)
        if ADDRESS_LEAD_RE.match(prefix or stripped):
            break
        if gmail_parser.EMAIL_RE.search(prefix):
            break
        if gmail_parser._parse_move_date(prefix) or _parse_calendar_date(prefix, _perth_today()):
            break
        candidate = _clean_name(prefix)
        if candidate and _is_plausible_person_name(candidate):
            return candidate
        if gmail_parser.PHONE_RE.search(stripped) or ADDRESS_LEAD_RE.search(stripped):
            break
        break
    return ""


def _extract_name(text: str) -> str:
    combined = _extract_first_last_name(text)
    if combined:
        return combined
    leading = _extract_leading_name(text)
    if leading:
        return leading
    for pattern in NAME_PATTERNS:
        match = pattern.search(text or "")
        if match:
            name = _clean_name(match.group(1))
            if _is_plausible_person_name(name):
                return name

    labelled = gmail_parser._first_match(
        gmail_parser.FIELD_LABELS.get("customer_name", ()),
        text,
    )
    if _is_plausible_person_name(labelled):
        return _clean_name(labelled)

    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped.split()) > 3:
            continue
        if gmail_parser.PHONE_RE.search(stripped):
            continue
        if gmail_parser.EMAIL_RE.search(stripped):
            continue
        if re.search(r"\b(?:from|to|moving|pickup|drop|email|phone|mobile)\b", stripped, re.I):
            continue
        if GREETING_LINE_RE.match(stripped):
            continue
        if ADDRESS_LEAD_RE.match(stripped):
            continue
        if gmail_parser._parse_move_date(stripped) or _parse_calendar_date(stripped, _perth_today()):
            continue
        if VAGUE_TIME_RE.search(stripped) or TIME_EXACT_RE.search(stripped):
            continue
        candidate = _clean_name(stripped)
        if _is_plausible_person_name(candidate):
            return candidate
    return ""


def normalize_au_mobile(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if STREET_HINT_RE.search(text):
        match = gmail_parser.PHONE_RE.search(text)
        if not match:
            return text
        digits = re.sub(r"\D", "", match.group(0))
    else:
        line_digits = re.sub(r"\D", "", text.splitlines()[0])
        if 9 <= len(line_digits) <= 12:
            digits = line_digits
        else:
            match = gmail_parser.PHONE_RE.search(text)
            if not match:
                return text
            digits = re.sub(r"\D", "", match.group(0))
    if digits.startswith("61") and len(digits) >= 11:
        digits = "0" + digits[2:]
    if len(digits) == 9 and digits.startswith("4"):
        digits = "0" + digits
    if len(digits) == 11 and digits.startswith("04"):
        for index in range(2, len(digits) - 1):
            if digits[index] == digits[index + 1]:
                candidate = digits[: index + 1] + digits[index + 2 :]
                if len(candidate) == 10 and candidate.startswith("04"):
                    digits = candidate
                    break
    if len(digits) == 10 and digits.startswith("0"):
        return "{0} {1} {2}".format(digits[:4], digits[4:7], digits[7:])
    return text


def _extract_phone(text: str) -> str:
    labelled = gmail_parser._first_match(
        gmail_parser.FIELD_LABELS.get("phone", ()),
        text,
    )
    labelled_mobile = gmail_parser._first_match(
        (
            r"(?:phone\s*number|mobile|phone|tel(?:ephone)?|contact\s*number)\s*[:\-]\s*(.+)",
        ),
        text,
    )
    for candidate in (labelled_mobile, labelled, text):
        normalized = normalize_au_mobile(candidate)
        if normalized and re.match(r"^04\d{2} \d{3} \d{3}$", normalized):
            return normalized
    return ""


def _extract_email(text: str) -> str:
    labelled = gmail_parser._first_match(
        gmail_parser.FIELD_LABELS.get("email", ()),
        text,
    )
    return gmail_parser._normalize_email(labelled or text)


def _next_weekday(reference: date, weekday: int, *, include_today: bool = False) -> date:
    days_ahead = (weekday - reference.weekday()) % 7
    if days_ahead == 0 and not include_today:
        days_ahead = 7
    return reference + timedelta(days=days_ahead)


def _parse_named_month_date(fragment: str, reference: date) -> Optional[date]:
    text = (fragment or "").strip()
    for match in re.finditer(
        r"\b(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{2,4}))?\b",
        text,
        re.IGNORECASE,
    ):
        day = int(match.group(1))
        month = MONTH_NAMES.get(match.group(2).lower())
        year = int(match.group(3)) if match.group(3) else reference.year
        if month:
            try:
                parsed = date(year, month, day)
                if not match.group(3) and parsed < reference:
                    parsed = date(reference.year + 1, month, day)
                return parsed
            except ValueError:
                continue

    for match in re.finditer(
        r"\b([A-Za-z]+)\s+(\d{1,2})(?:,?\s+(\d{2,4}))?\b",
        text,
        re.IGNORECASE,
    ):
        month = MONTH_NAMES.get(match.group(1).lower())
        day = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else reference.year
        if month:
            try:
                parsed = date(year, month, day)
                if not match.group(3) and parsed < reference:
                    parsed = date(reference.year + 1, month, day)
                return parsed
            except ValueError:
                continue
    return None


def _parse_relative_date(text: str, reference: date) -> Tuple[str, bool]:
    """Return (iso_date, ambiguous). Blank iso_date when unknown or ambiguous."""
    body = (text or "").strip()
    lower = body.lower()
    if not body:
        return "", False

    if DATE_AMBIGUOUS_RE.search(body):
        return "", True

    explicit = gmail_parser._parse_move_date(body)
    if explicit:
        return explicit, False

    named = _parse_named_month_date(body, reference)
    if named:
        return named.isoformat(), False

    if re.search(r"\btomorrow\b", lower):
        return (reference + timedelta(days=1)).isoformat(), False

    if re.search(r"\btoday\b", lower):
        return reference.isoformat(), False

    next_weekday = re.search(
        r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lower,
    )
    if next_weekday:
        weekday = WEEKDAY_NAMES[next_weekday.group(1)]
        target = _next_weekday(reference, weekday)
        if target <= reference:
            target += timedelta(days=7)
        return target.isoformat(), False

    this_weekday = re.search(
        r"\bthis\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lower,
    )
    if this_weekday:
        weekday = WEEKDAY_NAMES[this_weekday.group(1)]
        target = _next_weekday(reference, weekday, include_today=True)
        if target < reference:
            return "", True
        return target.isoformat(), False

    on_weekday = re.search(
        r"\b(?:on\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lower,
    )
    if on_weekday and not re.search(r"\bnext\s+week\b", lower):
        weekday = WEEKDAY_NAMES[on_weekday.group(1)]
        target = _next_weekday(reference, weekday)
        return target.isoformat(), False

    next_named = re.search(r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lower)
    if next_named:
        weekday = WEEKDAY_NAMES[next_named.group(1)]
        target = _next_weekday(reference, weekday)
        if target <= reference:
            target += timedelta(days=7)
        return target.isoformat(), False

    if re.search(r"\bnext\s+friday\b", lower):
        target = _next_weekday(reference, WEEKDAY_NAMES["friday"])
        if target <= reference:
            target += timedelta(days=7)
        return target.isoformat(), False

    if re.search(r"\bthis\s+friday\b", lower):
        target = _next_weekday(reference, WEEKDAY_NAMES["friday"], include_today=True)
        if target.weekday() != 4 or target < reference:
            return "", True
        return target.isoformat(), False

    return "", False


def _parse_calendar_date(fragment: str, reference: date) -> str:
    iso, _ambiguous = _parse_relative_date(fragment, reference)
    return iso


def _extract_move_date(text: str, reference: date) -> Tuple[str, bool]:
    labelled = gmail_parser._first_match(
        gmail_parser.FIELD_LABELS.get("move_date", ()),
        text,
    )
    for fragment in (labelled, text):
        iso, ambiguous = _parse_relative_date(fragment or "", reference)
        if ambiguous:
            return "", True
        if iso:
            return iso, False
    return "", False


def _split_parentheticals(value: str) -> Tuple[str, List[str]]:
    notes = []
    def _keep(match: re.Match) -> str:
        aside = (match.group(1) or "").strip(" ,;")
        if aside:
            notes.append(aside)
        return " "
    cleaned = PARENTHETICAL_RE.sub(_keep, value or "")
    return cleaned, notes


def _normalize_street_types(value: str) -> str:
    def _replace(match: re.Match) -> str:
        raw = match.group(0)
        key = raw.lower().rstrip(".")
        mapped = STREET_TYPE_NORMAL.get(key)
        if not mapped:
            return raw
        return mapped
    return STREET_TYPE_RE.sub(_replace, value or "")


def _cut_address_field_tail(value: str) -> str:
    text = (value or "").strip()
    text = re.split(
        r"\s+(?:phone|mobile|email|e-mail|tel(?:ephone)?|destination|deliver(?:y)?|"
        r"drop[\s-]?off|moving\s+to|\bto\b)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.sub(r"(?:\+?61|0)\d[\d\s]{7,}$", "", text).strip()
    return text


def _strip_address_schedule_tail(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    match = SCHEDULE_TAIL_START_RE.search(text)
    if match:
        text = text[: match.start()].rstrip(" ,;")
    return text


def _clean_address(value: str, asides: Optional[List[str]] = None) -> str:
    text = (value or "").strip()
    text, found = _split_parentheticals(text)
    if asides is not None:
        asides.extend(found)
    text = _cut_address_field_tail(text)
    text = re.sub(r"^(?:is|at)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[—–\-]+$", "", text).strip()
    text = text.rstrip(".,;")
    text = re.sub(r"\s+", " ", text)
    text = _strip_address_schedule_tail(text)
    text = _normalize_street_types(text)
    return text.strip(" ,;")


def _has_full_street_address(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    return bool(STREET_HINT_RE.search(text))


def _is_plausible_address(value: str) -> bool:
    text = _clean_address(value)
    if not text:
        return False
    lowered = text.lower().strip()
    if lowered in INVALID_ADDRESS_TOKENS:
        return False
    if len(lowered.split()) == 1 and lowered in INVALID_ADDRESS_TOKENS:
        return False
    if re.fullmatch(r"\+?\d[\d\s]{7,}", text):
        return False
    return bool(STREET_HINT_RE.search(text)) or bool(SUBURB_ONLY_RE.match(text))


def _labelled_address(text: str, patterns: Tuple[str, ...], asides: Optional[List[str]] = None) -> str:
    for pattern in patterns:
        match = gmail_parser._first_match((pattern,), text)
        if match:
            cleaned = _clean_address(match, asides)
            if _is_plausible_address(cleaned):
                return cleaned
    return ""


def _strip_pickup_bridge_tail(value: str) -> str:
    text = _clean_address(value)
    return re.sub(r"\s+and\s*$", "", text, flags=re.IGNORECASE).strip()


def _extract_dual_street_addresses(text: str, asides: Optional[List[str]] = None) -> Tuple[str, str]:
    body = text or ""
    for pattern in (NEW_ADDRESS_IS_RE, DELIVERY_INDICATOR_RE):
        match = pattern.search(body)
        if not match:
            continue
        pickup = _strip_pickup_bridge_tail(match.group("pickup"))
        delivery = _clean_address(match.group("delivery"), asides)
        pickup = _clean_address(pickup, asides)
        if _is_plausible_address(pickup) and _is_plausible_address(delivery):
            if ADDRESS_LEAD_RE.match(pickup) or re.match(r"^\d{8,}", pickup):
                continue
            return pickup, delivery
    return "", ""


def _location_result(
    pickup: str, delivery: str, asides: Optional[List[str]] = None
) -> Tuple[str, str, bool, bool, List[str]]:
    return (
        pickup,
        delivery,
        not _has_full_street_address(pickup),
        not _has_full_street_address(delivery),
        list(asides or []),
    )


def _extract_locations(text: str) -> Tuple[str, str, bool, bool, List[str]]:
    asides: List[str] = []
    labelled_pickup = _labelled_address(text, PICKUP_LABELS, asides)
    labelled_delivery = _labelled_address(text, DELIVERY_LABELS, asides)
    if labelled_pickup and labelled_delivery:
        return _location_result(labelled_pickup, labelled_delivery, asides)

    pickup, delivery = _extract_dual_street_addresses(text, asides)
    if pickup or delivery:
        pickup = pickup or labelled_pickup
        delivery = delivery or labelled_delivery
        return _location_result(pickup, delivery, asides)

    for pattern in (FROM_TO_LINE_RE, MOVING_FROM_TO_RE):
        match = pattern.search(text or "")
        if match:
            pickup = _clean_address(match.group(1), asides) or labelled_pickup
            delivery = _clean_address(match.group(2), asides) or labelled_delivery
            return _location_result(pickup, delivery, asides)

    street_match = STREET_TO_STREET_RE.search(text or "")
    if street_match:
        pickup = _clean_address(street_match.group(1), asides) or labelled_pickup
        delivery = _clean_address(street_match.group(2), asides) or labelled_delivery
        return _location_result(pickup, delivery, asides)

    if labelled_pickup or labelled_delivery:
        return _location_result(labelled_pickup, labelled_delivery, asides)

    to_match = STANDALONE_TO_RE.search(text or "")
    if to_match:
        delivery = _clean_address(to_match.group(1), asides)
        return _location_result("", delivery, asides)

    sms_pickup, sms_delivery = sms_inbound_parser._extract_locations(text)
    if sms_pickup or sms_delivery:
        pickup = _clean_address(sms_pickup.replace(", WA", ""), asides)
        delivery = _clean_address(sms_delivery.replace(", WA", ""), asides)
        return _location_result(pickup, delivery, asides)

    moving_match = MOVING_TO_RE.search(text or "")
    if moving_match:
        delivery = _clean_address(moving_match.group(1), asides)
        before = (text or "")[: moving_match.start()].strip().splitlines()
        pickup = ""
        for line in reversed(before):
            line = line.strip()
            if not line:
                continue
            if re.search(r"\b(?:this\s+is|hi\s+|hello\b|thanks\b)", line, re.IGNORECASE):
                continue
            if gmail_parser.PHONE_RE.search(line):
                continue
            if _parse_calendar_date(line, _perth_today()):
                continue
            pickup = _clean_address(line, asides)
            break
        return _location_result(pickup, delivery, asides)

    return _location_result("", "", asides)


def _parse_start_time_24h(value: str) -> str:
    text = (value or "").strip().lower().replace(".", "")
    if not text:
        return ""
    if re.fullmatch(r"\d{1,2}", text):
        hour = int(text)
        if 1 <= hour <= 12:
            return "{0:02d}:00".format(hour)
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour, minute = text.split(":")
        return "{0:02d}:{1}".format(int(hour), minute)
    for fmt in ("%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%H:%M")
        except ValueError:
            continue
    return ""


def _extract_start_time(text: str) -> Tuple[str, bool, bool]:
    """Return (time_24h, approximate, vague_only)."""
    body = text or ""
    lower = body.lower()

    if VAGUE_TIME_RE.search(lower) and not TIME_EXACT_RE.search(body) and not TIME_APPROX_RE.search(body):
        return "", False, True

    for pattern in (TIME_START_LABEL_RE, TIME_EXACT_RE):
        match = pattern.search(body)
        if match:
            parsed = _parse_start_time_24h(match.group(1))
            if parsed:
                return parsed, False, False

    approx = TIME_APPROX_RE.search(body)
    if approx:
        parsed = _parse_start_time_24h(approx.group(1))
        if parsed:
            return parsed, True, False

    after = TIME_AFTER_RE.search(body)
    if after:
        parsed = _parse_start_time_24h(after.group(1))
        if parsed:
            return parsed, True, False

    return "", False, False


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


def _structured_line(line: str, parsed: Dict[str, Any]) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return True
    if LABELLED_FIELD_LINE_RE.match(stripped):
        return True
    first_name = FIRST_NAME_RE.search(stripped)
    last_name = LAST_NAME_RE.search(stripped)
    if first_name and _clean_name(first_name.group(1)) in (parsed.get("customer_name") or ""):
        return True
    if last_name and _clean_name(last_name.group(1)) in (parsed.get("customer_name") or ""):
        return True
    if _line_matches_field(stripped, parsed.get("customer_name", "")):
        return True
    if gmail_parser.PHONE_RE.search(stripped) or _line_matches_field(stripped, parsed.get("phone", "")):
        return True
    if gmail_parser._normalize_email(stripped):
        return True
    if _line_matches_field(stripped, parsed.get("delivery_address", "")):
        return True
    if NEW_ADDRESS_IS_RE.search(stripped) or DELIVERY_INDICATOR_RE.search(stripped):
        return True
    pickup = (parsed.get("pickup_address") or "").strip()
    delivery = (parsed.get("delivery_address") or "").strip()
    if pickup and pickup in stripped:
        return True
    if delivery and delivery in stripped:
        return True
    if _line_matches_field(stripped, parsed.get("pickup_address", "")):
        return True
    if FROM_TO_LINE_RE.search(stripped) or MOVING_FROM_TO_RE.search(stripped):
        return True
    if MOVING_TO_RE.search(stripped):
        return True
    if _parse_calendar_date(stripped, _perth_today()):
        return True
    if TIME_START_LABEL_RE.search(stripped) or TIME_EXACT_RE.search(stripped) or TIME_APPROX_RE.search(stripped):
        return True
    if re.search(r"^\s*(?:hi|hello|thanks|thank you|regards|cheers)\b", stripped, re.I):
        return True
    if re.search(r"\b(?:pickup|drop\s*off|delivery|mobile|phone|email)\s*[:\-]", stripped, re.I):
        return True
    return False


def _scrub_structured_text(text: str, parsed: Dict[str, Any]) -> str:
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _structured_line(stripped, parsed):
            continue
        scrubbed = stripped
        for key in ("pickup_address", "delivery_address", "customer_name", "phone", "email"):
            value = (parsed.get(key) or "").strip()
            if value and value.lower() in scrubbed.lower():
                scrubbed = re.sub(re.escape(value), " ", scrubbed, flags=re.IGNORECASE)
        lines.append(scrubbed.strip())
    return "\n".join(line for line in lines if line)


def _keyword_note_lines(text: str, parsed: Dict[str, Any]) -> List[str]:
    scrubbed = _scrub_structured_text(text, parsed)
    lines = []
    for label, pattern in NOTE_KEYWORDS:
        if pattern.search(scrubbed):
            lines.append(label.capitalize() if label == label.lower() else label)
    deduped = []
    seen = set()
    for line in lines:
        key = line.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(line)
    return deduped


def _extract_notes(text: str, parsed: Dict[str, Any], asides: Optional[List[str]] = None) -> str:
    keyword_lines = _keyword_note_lines(text, parsed)
    remaining = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _structured_line(stripped, parsed):
            continue
        remaining.append(stripped)
    for aside in asides or []:
        cleaned = (aside or "").strip()
        if not cleaned:
            continue
        if _structured_line(cleaned, parsed):
            continue
        duplicate = False
        for key in ("customer_name", "phone", "email", "pickup_address", "delivery_address"):
            value = (parsed.get(key) or "").strip()
            if value and value.lower() == cleaned.lower():
                duplicate = True
                break
        if duplicate:
            continue
        if cleaned not in remaining:
            remaining.append(cleaned)

    sections = []
    if keyword_lines:
        sections.append("\n".join("- {0}".format(item) for item in keyword_lines))
    if remaining:
        filtered = []
        for line in remaining:
            if keyword_lines and any(pattern.search(line) for _, pattern in NOTE_KEYWORDS):
                continue
            filtered.append(line)
        if filtered:
            sections.append("\n".join(filtered))
    return "\n\n".join(section for section in sections if section).strip()


def _build_warnings(parsed: Dict[str, Any]) -> List[str]:
    warnings = list(parsed.get("_warnings") or [])
    if not (parsed.get("phone") or "").strip():
        warnings.append("Phone number not found")
    if parsed.get("_date_ambiguous"):
        warnings.append("Date could not be confidently determined")
    elif not (parsed.get("move_date") or "").strip():
        warnings.append("Move date not found")
    if parsed.get("_time_vague"):
        warnings.append("Time is vague — please enter an exact start time")
    elif parsed.get("_time_approximate") and (parsed.get("start_time") or "").strip():
        warnings.append("Time is approximate")
    elif not (parsed.get("start_time") or "").strip() and re.search(
        r"\b(?:morning|afternoon|around|about|after|at\s+\d)\b",
        parsed.get("source_text") or "",
        re.I,
    ):
        warnings.append("Start time not found")
    if parsed.get("_pickup_suburb_only") and (parsed.get("pickup_address") or "").strip():
        warnings.append("Full pickup street address not found")
    if parsed.get("_delivery_suburb_only") and (parsed.get("delivery_address") or "").strip():
        warnings.append("Full delivery street address not found")
    if not (parsed.get("customer_name") or "").strip():
        warnings.append("Customer name not found")
    deduped = []
    seen = set()
    for warning in warnings:
        if warning not in seen:
            seen.add(warning)
            deduped.append(warning)
    return deduped


def parse_pasted_text(
    text: str,
    *,
    reference: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Parse free-form pasted enquiry text into booking fields."""
    message = (text or "").strip()
    ref_date = _perth_today(reference)
    empty = {
        "customer_name": "",
        "phone": "",
        "email": "",
        "move_date": "",
        "pickup_address": "",
        "delivery_address": "",
        "start_time": "",
        "notes": "",
        "warnings": [],
        "source_text": message,
        "confidence": 0.0,
    }
    if not message:
        return empty

    date_source, _paren_notes = _split_parentheticals(message)
    sms_name = sms_inbound_parser._extract_name(message)
    customer_name = _pick(_extract_name(message), _clean_name(sms_name))
    phone = _extract_phone(message)
    email = _extract_email(message)
    move_date, date_ambiguous = _extract_move_date(date_source, ref_date)

    pickup, delivery, pickup_suburb_only, delivery_suburb_only, asides = _extract_locations(message)

    start_time, time_approximate, time_vague = _extract_start_time(date_source)

    interim = {
        "customer_name": customer_name,
        "phone": phone,
        "email": email,
        "move_date": move_date,
        "pickup_address": pickup,
        "delivery_address": delivery,
    }
    notes = _extract_notes(message, interim, asides + _paren_notes)

    fields = {
        "customer_name": customer_name,
        "phone": phone,
        "email": email,
        "move_date": move_date,
        "pickup_address": pickup,
        "delivery_address": delivery,
        "start_time": start_time,
        "notes": notes,
        "source_text": message,
        "_date_ambiguous": date_ambiguous,
        "_time_approximate": time_approximate,
        "_time_vague": time_vague,
        "_pickup_suburb_only": pickup_suburb_only,
        "_delivery_suburb_only": delivery_suburb_only,
    }
    fields["warnings"] = _build_warnings(fields)
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
        "start_time": (parsed.get("start_time") or "").strip(),
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
    rows = [
        ("Name", (parsed.get("customer_name") or "").strip() or "—"),
        ("Phone", (parsed.get("phone") or "").strip() or "—"),
        ("Email", (parsed.get("email") or "").strip() or "—"),
        ("From", (parsed.get("pickup_address") or "").strip() or "—"),
        ("To", (parsed.get("delivery_address") or "").strip() or "—"),
        ("Date", _format_move_date_display(parsed.get("move_date") or "")),
        ("Start Time", format_start_time_display(parsed.get("start_time") or "")),
        ("Notes", _truncate_notes(parsed.get("notes") or "")),
    ]
    return rows


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
