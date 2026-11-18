"""WG Order Import: read WG delivery orders from an uploaded photo / screenshot / PDF.

Nothing here saves a booking. analyse_upload() returns one WG Day (build_day:
date, crew, truck, stops with the wg_orders.STOP_KEYS fields) plus per-field
warnings; the WG Orders page shows it as the editable WG Day form and the
owner saves it with "Save WG Day".

Two readers, same post-processing:

- Text PDFs: the PDF's own text (PyMuPDF) → parse_text(), a rule-based parser
  for WG's "Name: / Products: / Date/Time: / Address: / Phone: / Payment: /
  Ready: / Notes:" blocks under "Yasu 1", "Yasu 2" … headings.
- Photos, screenshots, scanned PDFs (or a text PDF the rules can't read):
  Claude vision (ANTHROPIC_API_KEY, model WG_IMPORT_MODEL). It is told to copy
  only what it can read and to list unreadable / unsure fields, never guess.

normalize_orders() then turns raw values into form values (times, date, payment
amount + type, crew, truck) and adds warnings ("Time unclear", "Address may
need review", "Phone number uncertain" …; duplicates are marked by
wg_orders.annotate_duplicates). Anything it
is not sure about is left for the owner to check instead of being "fixed".

Uploads are checked by extension, size and file signature, read in memory and
never written to disk or given a URL.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from werkzeug.utils import secure_filename

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 20
ALLOWED = {
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".pdf": ("application/pdf", (b"%PDF-",)),
}
DEFAULT_MODEL = "claude-sonnet-5-5"
API_URL = "https://api.anthropic.com/v1/messages"
API_TIMEOUT_SECONDS = 100

WARN_TIME = "Time unclear — check start / finish"
WARN_TIME_PM = "Time read as PM — check"
WARN_DATE = "Date unclear — check"
WARN_DATE_WEEKDAY = "Weekday doesn't match the date — check"
WARN_ADDRESS = "Address may need review"
WARN_PHONE = "Phone number uncertain"
WARN_PAYMENT = "Payment unclear — check amount / type"
WARN_CREW = "Crew not clear — choose the crew"
WARN_NAME = "Customer name unclear"


class ImportError_(Exception):
    """A problem the owner should see (bad file, analysis not available, …)."""


# --- upload checks -------------------------------------------------------------------


def check_upload(filename: str, data: bytes) -> Tuple[str, str]:
    """(safe filename, kind "image"/"pdf"). Raises ImportError_ for anything not allowed."""
    safe = secure_filename(filename or "") or "upload"
    ext = os.path.splitext(safe)[1].lower()
    if ext not in ALLOWED:
        raise ImportError_("Only JPG, JPEG, PNG or PDF files can be imported.")
    if not data:
        raise ImportError_("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImportError_("The file is too large (max {0} MB).".format(MAX_UPLOAD_BYTES // (1024 * 1024)))
    _mime, signatures = ALLOWED[ext]
    if not any(data.startswith(sig) for sig in signatures):
        raise ImportError_("The file content doesn't match its type ({0}).".format(ext))
    return safe, "pdf" if ext == ".pdf" else "image"


def media_type(filename: str) -> str:
    return ALLOWED[os.path.splitext(filename)[1].lower()][0]


# --- text PDFs ------------------------------------------------------------------------


def pdf_text(data: bytes) -> Tuple[str, int]:
    """All text of a PDF (pages joined) and the page count."""
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if doc.page_count > MAX_PDF_PAGES:
            raise ImportError_("The PDF has {0} pages — import at most {1} at a time.".format(doc.page_count, MAX_PDF_PAGES))
        return "\n".join(page.get_text() for page in doc), doc.page_count


_LABELS = {
    "customer": r"name|customer(?:\s+name)?",
    "products": r"products?|items?",
    "datetime": r"date\s*/\s*time|date\s*&\s*time",
    "date": r"date",
    "time": r"time|delivery\s+time",
    "address": r"address|delivery\s+address",
    "phone": r"phone|mobile|ph|contact",
    "payment": r"payment|pay|amount",
    "reference": r"ready|reference|ref|rex",
    "notes": r"notes?|comments?|instructions?",
    "truck": r"truck",
    "crew": r"crew|driver|staff",
}
_LABEL_RE = re.compile(
    r"^\s*(?P<label>{0})\s*[:\-–]\s*(?P<value>.*)$".format("|".join("(?:{0})".format(v) for v in _LABELS.values())),
    re.IGNORECASE,
)
_HEADER_RE = re.compile(r"^\s*(?P<word>[A-Za-z]+|#)\s*#?\s*(?P<n>\d{1,2})\s*$", re.IGNORECASE)
_HEADER_WORDS = ("job", "order", "wg", "delivery", "#")
_DAY_HEADING_RE = re.compile(
    r"^\s*(?P<text>(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+\d{1,2}(?:st|nd|rd|th)?\s+[a-z]+(?:\s+\d{4})?)\s*$",
    re.IGNORECASE,
)
_CREW_HEADING_RE = re.compile(r"^\s*(?P<crew>[A-Z][A-Za-z]{1,15})\s*$")


def _label_key(label: str) -> str:
    text = label.strip().lower()
    for key, pattern in _LABELS.items():
        if re.fullmatch(pattern, text, re.IGNORECASE):
            return key
    return ""


def parse_text(text: str, crew_names: List[str]) -> Dict[str, Any]:
    """
    Rule-based reader for WG's labelled blocks. Returns {"orders": [...], "date_text": ...}.
    A new order starts at a "Yasu 1" / "Order 2" style heading or at a "Name:" line.
    """
    lower_crew = {name.lower(): name for name in crew_names}
    orders: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    last_key = ""
    date_text = ""
    heading_crew = ""

    def start(crew_hint: str = "") -> Dict[str, Any]:
        order: Dict[str, Any] = {"crew_hint": crew_hint or heading_crew, "source": "text", "uncertain": []}
        orders.append(order)
        return order

    for raw_line in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        day = _DAY_HEADING_RE.match(line)
        if day:
            date_text = date_text or day.group("text")
            last_key = ""
            continue
        header = _HEADER_RE.match(line)
        if header and (header.group("word").lower() in lower_crew or header.group("word").lower() in _HEADER_WORDS):
            current = start(lower_crew.get(header.group("word").lower(), ""))
            last_key = ""
            continue
        only_crew = _CREW_HEADING_RE.match(line)
        if only_crew and only_crew.group("crew").lower() in lower_crew and not _LABEL_RE.match(line):
            heading_crew = lower_crew[only_crew.group("crew").lower()]
            if current is not None and not current.get("customer"):
                current["crew_hint"] = heading_crew
            last_key = ""
            continue
        labelled = _LABEL_RE.match(line)
        if labelled:
            key = _label_key(labelled.group("label"))
            value = labelled.group("value").strip()
            if key == "customer" and (current is None or current.get("customer")):
                current = start()
            if current is None:
                current = start()
            if key == "products":
                current["products"] = value
            elif key in ("notes",):
                current["notes"] = value
            else:
                current[key] = value
            last_key = key
            continue
        # Continuation line: more products / notes / a wrapped address.
        if current is not None and last_key in ("products", "notes", "address"):
            joiner = ", " if last_key == "address" else "\n"
            current[last_key] = (current.get(last_key) + joiner + line).strip(", \n") if current.get(last_key) else line
    orders = [o for o in orders if any(o.get(k) for k in ("customer", "address", "products", "phone"))]
    return {"orders": orders, "date_text": date_text}


# --- Claude vision ----------------------------------------------------------------------

_PROMPT = """You read delivery orders for "WG Outdoor Life" (an outdoor furniture shop) from a photo, screenshot or PDF.
The file usually holds several orders (often a table, one row or block per order, e.g. "Yasu 1", "Yasu 2").
Copy what is written. Do NOT guess or invent anything you cannot read clearly.

Return ONLY a JSON object, no prose:
{
  "date_text": "the day heading if one date covers all orders (e.g. \\"Friday 20th November\\"), else \\"\\"",
  "orders": [
    {
      "crew_hint": "staff name written for this order or its group/column (e.g. \\"Yasu\\"), else \\"\\"",
      "customer": "customer name",
      "products": "one product per line, keep the quantities, separated by \\n",
      "date": "date written on this order, else \\"\\"",
      "time": "time exactly as written, e.g. \\"10-11:30\\" or \\"11:30-1:30\\"",
      "address": "delivery address",
      "phone": "phone number",
      "payment": "payment exactly as written, e.g. \\"YASU $320\\", \\"$330 CONFIRMED\\", \\"$220 COD\\"",
      "reference": "Ready / REX reference exactly as written, e.g. \\"REX 1649 AND REX 2359\\"",
      "notes": "notes / instructions",
      "truck": "truck only if the file names one, else \\"\\"",
      "uncertain": ["names of fields you could not read clearly: customer, products, date, time, address, phone, payment, reference, notes"]
    }
  ],
  "unreadable": "short note if part of the file could not be read, else \\"\\""
}
Use "" for anything not in the file. Keep every order, in the order they appear."""


def ai_available() -> bool:
    return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())


def _extract_json(text: str) -> Dict[str, Any]:
    body = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    if fence:
        body = fence.group(1)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < 0:
        raise ImportError_("The analysis didn't return any orders.")
    try:
        return json.loads(body[start : end + 1])
    except ValueError as exc:
        raise ImportError_("The analysis result couldn't be read ({0}).".format(exc)) from exc


def analyse_with_ai(data: bytes, filename: str, kind: str) -> Dict[str, Any]:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise ImportError_(
            "Photo / scanned-file analysis is not set up yet (ANTHROPIC_API_KEY is missing). "
            "Text PDFs can still be imported."
        )
    encoded = base64.standard_b64encode(data).decode("ascii")
    if kind == "pdf":
        block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": encoded}}
    else:
        block = {"type": "image", "source": {"type": "base64", "media_type": media_type(filename), "data": encoded}}
    payload = {
        "model": (os.environ.get("WG_IMPORT_MODEL") or DEFAULT_MODEL).strip(),
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": [block, {"type": "text", "text": _PROMPT}]}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_SECONDS) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            pass
        raise ImportError_("The analysis service returned an error ({0}){1}.".format(exc.code, ": " + detail if detail else "")) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ImportError_("The analysis service could not be reached — try again.") from exc
    text = "".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")
    parsed = _extract_json(text)
    orders = [dict(o, source="ai") for o in parsed.get("orders") or [] if isinstance(o, dict)]
    return {"orders": orders, "date_text": parsed.get("date_text") or "", "unreadable": parsed.get("unreadable") or ""}


# --- normalising -----------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_WEEKDAYS = {d: i for i, d in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])}


def parse_date_text(text: Any, today: date) -> Tuple[str, Optional[str]]:
    """("YYYY-MM-DD", warning) from "Friday 20th November", "20/11", "2026-11-20"; ("", warning) if unclear."""
    raw = str(text or "").strip()
    if not raw:
        return "", None
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))).isoformat(), None
        except ValueError:
            return "", WARN_DATE
    lower = raw.lower()
    weekday = None
    wd = re.match(r"\s*(mon|tue|wed|thu|fri|sat|sun)", lower)
    if wd:
        weekday = _WEEKDAYS[wd.group(1)]
    day = month = year = None
    named = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3,})\.?(?:\s+(\d{4}))?", lower)
    if named and named.group(2)[:3] in _MONTHS:
        day, month = int(named.group(1)), _MONTHS[named.group(2)[:3]]
        year = int(named.group(3)) if named.group(3) else None
    else:
        slash = re.search(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", lower)
        if slash:
            day, month = int(slash.group(1)), int(slash.group(2))
            if slash.group(3):
                year = int(slash.group(3)) + (2000 if len(slash.group(3)) == 2 else 0)
    if not day or not month:
        return "", WARN_DATE
    try:
        if year:
            found = date(year, month, day)
        else:
            # The nearest such date from a week ago onwards (orders are for the coming days).
            found = date(today.year, month, day)
            if found < today - timedelta(days=7):
                found = date(today.year + 1, month, day)
    except ValueError:
        return "", WARN_DATE
    if weekday is not None and found.weekday() != weekday:
        return found.isoformat(), WARN_DATE_WEEKDAY
    return found.isoformat(), None


def _hm(hour: int, minute: int) -> str:
    return "{0:02d}:{1:02d}".format(hour, minute)


_TIME_PART = r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"


def parse_time_range(text: Any) -> Tuple[str, str, Optional[str]]:
    """
    ("10:00", "11:30", warning) from "10–11:30", "11:30-1:30", "1:00 - 3:00pm", "10am".
    Delivery hours are 7am–7pm: a bare 1–6 start is read as PM (with a warning to check).
    """
    raw = str(text or "").strip().lower().replace("–", "-").replace("—", "-").replace(" to ", "-")
    if not raw:
        return "", "", None
    match = re.search(_TIME_PART + r"\s*(?:-\s*" + _TIME_PART + r")?", raw)
    if not match:
        return "", "", WARN_TIME
    sh, sm, sap, fh, fm, fap = match.groups()
    sap = (sap or "").replace(".", "")
    fap = (fap or "").replace(".", "")
    start_h, start_m = int(sh), int(sm or 0)
    if start_h > 23 or start_m > 59:
        return "", "", WARN_TIME
    warning = None
    if not sap and fap == "pm" and fh and start_h < 12 and start_h <= int(fh) % 12:
        sap = "pm"  # "1-3pm" → both PM; "10-1pm" keeps 10am
    if sap == "pm" and start_h < 12:
        start_h += 12
    elif sap == "am" and start_h == 12:
        start_h = 0
    elif not sap and 1 <= start_h <= 6:
        start_h += 12
        warning = WARN_TIME_PM
    start = _hm(start_h, start_m)
    if not fh:
        return start, "", warning
    finish_h, finish_m = int(fh), int(fm or 0)
    if finish_h > 23 or finish_m > 59:
        return start, "", WARN_TIME
    if fap == "pm" and finish_h < 12:
        finish_h += 12
    elif fap == "am" and finish_h == 12:
        finish_h = 0
    elif not fap and finish_h < 12 and finish_h * 60 + finish_m <= start_h * 60 + start_m:
        finish_h += 12  # "11:30-1:30" → 13:30
    if finish_h * 60 + finish_m <= start_h * 60 + start_m:
        return start, "", WARN_TIME
    return start, _hm(finish_h, finish_m), warning


_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)|\b([\d,]+(?:\.\d{1,2})?)\s*(?:aud|dollars)\b", re.IGNORECASE)
_PAYMENT_WORDS = {"cod": "COD", "confirmed": "Confirmed", "invoice": "Invoice", "paid": "Confirmed"}


def parse_payment(text: Any) -> Tuple[str, str, Optional[str]]:
    """("320", "YASU", None) from "YASU $320"; ("330", "Confirmed", None) from "$330 CONFIRMED"."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return "", "", None
    money = _MONEY_RE.search(raw)
    amount = ""
    if money:
        amount = (money.group(1) or money.group(2) or "").replace(",", "")
    rest = _MONEY_RE.sub(" ", raw)
    words = [w for w in re.split(r"[^A-Za-z]+", rest) if w]
    ptype = ""
    for word in words:
        if word.lower() in _PAYMENT_WORDS:
            ptype = _PAYMENT_WORDS[word.lower()]
            break
    if not ptype and words:
        ptype = " ".join(words).upper()[:30]
    warning = None if amount else WARN_PAYMENT
    return amount, ptype, warning


def _phone_ok(phone: str) -> bool:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("61"):
        digits = "0" + digits[2:]
    return len(digits) == 10 and digits.startswith("0")


def _address_ok(address: str) -> bool:
    text = (address or "").strip()
    return bool(re.search(r"\d", text)) and bool(re.search(r"[A-Za-z]{3,}", text)) and len(text) >= 8


def _match_name(value: str, names: List[str]) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    for name in names:
        if text == name.lower():
            return name
    words = re.findall(r"[a-z]+", text)
    hits = [name for name in names if name.lower() in words]
    return hits[0] if len(hits) == 1 else ""


def normalize_orders(
    raw_orders: List[Dict[str, Any]],
    *,
    date_text: str = "",
    crew_names: List[str],
    truck_names: List[str],
    today: date,
    default_date: str = "",
) -> List[Dict[str, Any]]:
    """Raw readings → WG order form values + {"field_warnings": {field: [msg]}}."""
    shared_date, shared_warning = parse_date_text(date_text, today)
    out = []
    for raw in raw_orders:
        warnings: Dict[str, List[str]] = {}

        def warn(field: str, msg: str) -> None:
            warnings.setdefault(field, [])
            if msg not in warnings[field]:
                warnings[field].append(msg)

        uncertain = {str(f).strip().lower() for f in raw.get("uncertain") or []}
        customer = " ".join(str(raw.get("customer") or "").split())
        if not customer or "customer" in uncertain:
            warn("customer", WARN_NAME)

        move_date, date_warning = parse_date_text(raw.get("date"), today)
        if not move_date and raw.get("datetime"):
            move_date, date_warning = parse_date_text(raw.get("datetime"), today)
        if not move_date:
            move_date, date_warning = shared_date, shared_warning
        if not move_date:
            move_date, date_warning = default_date, WARN_DATE
        if date_warning or "date" in uncertain:
            warn("move_date", date_warning or WARN_DATE)

        time_text = raw.get("time") or ""
        if not time_text and raw.get("datetime"):
            time_text = re.sub(r"\b\d{1,2}[/.]\d{1,2}(?:[/.]\d{2,4})?\b", " ", str(raw["datetime"]))
            time_text = re.sub(r"(?i)\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b|\b\d{1,2}(st|nd|rd|th)\b", " ", time_text)
        start, finish, time_warning = parse_time_range(time_text)
        if not start:
            warn("start_time", time_warning or WARN_TIME)
        elif time_warning or "time" in uncertain:
            warn("start_time", time_warning or WARN_TIME)

        address = " ".join(str(raw.get("address") or "").split())
        if not _address_ok(address) or "address" in uncertain:
            warn("address", WARN_ADDRESS)

        phone = " ".join(str(raw.get("phone") or "").split())
        if (phone and not _phone_ok(phone)) or "phone" in uncertain:
            warn("phone", WARN_PHONE)

        amount, ptype, pay_warning = parse_payment(raw.get("payment"))
        if pay_warning or "payment" in uncertain:
            warn("payment_amount", pay_warning or WARN_PAYMENT)

        crew_hint = str(raw.get("crew_hint") or raw.get("crew") or "").strip()
        crew_name = _match_name(crew_hint, crew_names)
        if not crew_name:
            warn("crew", WARN_CREW if not crew_hint else "Crew “{0}” not found — choose the crew".format(crew_hint))

        truck_text = str(raw.get("truck") or "").strip()
        truck = _match_name(truck_text, truck_names) if truck_text else ""
        if truck_text and not truck:
            warn("truck", "Truck “{0}” not found — choose the truck".format(truck_text))

        products = str(raw.get("products") or "").replace("\r\n", "\n").strip()
        if "products" in uncertain:
            warn("products", "Products may need review")
        notes = str(raw.get("notes") or "").replace("\r\n", "\n").strip()
        if "notes" in uncertain:
            warn("notes", "Notes may need review")
        reference = " ".join(str(raw.get("reference") or "").split())
        if "reference" in uncertain:
            warn("reference", "Reference may need review")

        out.append(
            {
                "customer": customer,
                "products": products,
                "move_date": move_date,
                "start_time": start,
                "finish_time": finish,
                "address": address,
                "phone": phone,
                "payment_amount": amount,
                "payment_amount_raw": amount,
                "payment_type": ptype,
                "reference": reference,
                "notes": notes,
                "crew": [crew_name] if crew_name else [],
                "truck": truck,
                "field_warnings": warnings,
                "imported": True,
            }
        )
    return out


DAY_KEYS = ("move_date", "crew", "truck")


def build_day(orders: List[Dict[str, Any]], *, default_date: str = "") -> Dict[str, Any]:
    """
    Normalised readings → one WG Day: the sheet's date, the crew named on it (only
    names that match an active crew member), the truck if one is named, and the
    orders as stops. Unclear day fields get yellow warnings; nothing is guessed.
    """
    from collections import Counter

    day_warnings: Dict[str, List[str]] = {}

    def warn(field: str, msg: str) -> None:
        day_warnings.setdefault(field, [])
        if msg not in day_warnings[field]:
            day_warnings[field].append(msg)

    dates = Counter(o["move_date"] for o in orders if o.get("move_date"))
    if dates:
        top = max(dates.values())
        move_date = sorted(d for d, n in dates.items() if n == top)[0]
    else:
        move_date = default_date
        warn("move_date", WARN_DATE)
    crew: List[str] = []
    trucks: List[str] = []
    stops = []
    for order in orders:
        warnings = dict(order.get("field_warnings") or {})
        for msg in warnings.pop("move_date", []):
            warn("move_date", msg)
        crew_msgs = warnings.pop("crew", [])
        for msg in warnings.pop("truck", []):
            warn("truck", msg)
        for name in order.get("crew") or []:
            if name not in crew:
                crew.append(name)
        if not order.get("crew"):
            for msg in crew_msgs:
                if "not found" in msg:
                    warn("crew", msg)
        if order.get("truck") and order["truck"] not in trucks:
            trucks.append(order["truck"])
        if order.get("move_date") and order["move_date"] != move_date:
            warnings.setdefault("start_time", []).append(
                "The sheet gives this stop another date ({0}) — check".format(order["move_date"])
            )
        stop = {k: v for k, v in order.items() if k not in DAY_KEYS}
        stop["field_warnings"] = warnings
        stops.append(stop)
    if not crew:
        warn("crew", WARN_CREW)
    truck = trucks[0] if len(trucks) == 1 else ""
    if len(trucks) > 1:
        warn("truck", "The sheet names more than one truck ({0}) — choose one".format(", ".join(trucks)))
    return {"move_date": move_date, "crew": crew, "truck": truck, "stops": stops, "field_warnings": day_warnings}


# --- entry point -------------------------------------------------------------------------


def analyse_upload(
    filename: str,
    data: bytes,
    *,
    crew_names: List[str],
    truck_names: List[str],
    today: date,
    default_date: str = "",
) -> Dict[str, Any]:
    """Check the file, read the orders, normalise them into one WG Day. Never saves anything."""
    safe, kind = check_upload(filename, data)
    readings: Dict[str, Any] = {"orders": [], "date_text": ""}
    method = ""
    notices: List[str] = []
    if kind == "pdf":
        try:
            text, pages = pdf_text(data)
        except ImportError_:
            raise
        except Exception as exc:  # noqa: BLE001 — a damaged PDF
            raise ImportError_("The PDF couldn't be opened.") from exc
        notices.append("{0} page{1}".format(pages, "" if pages == 1 else "s"))
        if text.strip():
            readings = parse_text(text, crew_names)
            method = "text"
    if not readings["orders"]:
        readings = analyse_with_ai(data, safe, kind)
        method = "ai"
        if readings.get("unreadable"):
            notices.append("Not readable: {0}".format(readings["unreadable"]))
    orders = normalize_orders(
        readings["orders"],
        date_text=readings.get("date_text") or "",
        crew_names=crew_names,
        truck_names=truck_names,
        today=today,
        default_date=default_date,
    )
    day = build_day(orders, default_date=default_date) if orders else None
    return {"filename": safe, "kind": kind, "method": method, "orders": orders, "day": day, "notices": notices}
