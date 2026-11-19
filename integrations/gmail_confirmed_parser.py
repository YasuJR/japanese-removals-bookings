"""Read a sent "Confirmed" email thread and extract booking details (company_gmail.py).

Only the new text of each message is used (quoted replies are cut off), oldest first,
so the conversation reads once. Claude (ANTHROPIC_API_KEY) classifies the last
message (booking / amendment / payment / cancellation / other) and returns the
booking fields. It is told never to guess: anything not clearly stated comes back
null and listed in "missing"; anything ambiguous is listed in "uncertain".
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
API_TIMEOUT_SECONDS = 120
MAX_MESSAGE_CHARS = 6000
MAX_CONTEXT_CHARS = 12000

KINDS = ("booking", "amendment", "payment", "cancellation", "other")

# Booking form fields the review highlights (names match templates/_booking_form.html).
FIELD_NAMES = (
    "customer_name",
    "email",
    "phone",
    "move_date",
    "start_time",
    "pickup_address",
    "delivery_address",
    "num_movers",
    "hourly_rate",
    "callout_fee",
    "callout_minutes",
    "additional_charges",
    "furniture",
    "notes",
)

_CONFIRMED_RE = re.compile(r"\bconfirmed\b", re.IGNORECASE)
_QUOTE_SPLIT_RE = re.compile(
    r"\n\s*(?:On [^\n]{5,250}?(?:\n[^\n]{0,80}?)?wrote:|-{2,} ?Original Message|Begin forwarded message|From: [^\n]+\nSent: )",
    re.IGNORECASE,
)


class ParseError(Exception):
    """Extraction could not run (no key, service error, unreadable answer)."""


# --- message text -------------------------------------------------------------------


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", "replace")


def _find_part(payload: Dict[str, Any], mime: str) -> str:
    if payload.get("mimeType") == mime and (payload.get("body") or {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts") or []:
        found = _find_part(part, mime)
        if found:
            return found
    return ""


def attachment_names(payload: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    if payload.get("filename"):
        names.append(payload["filename"])
    for part in payload.get("parts") or []:
        names.extend(attachment_names(part))
    return names


def _html_to_text(raw: str) -> str:
    raw = re.split(r"<blockquote", raw, maxsplit=1, flags=re.IGNORECASE)[0]
    raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(div|p|tr|li|h\d)>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", "", raw))


def new_text(payload: Dict[str, Any]) -> str:
    """The text the sender wrote in this message, without the quoted earlier emails."""
    plain = _find_part(payload, "text/plain")
    if plain:
        text = "\n".join(line for line in plain.splitlines() if not line.lstrip().startswith(">"))
    else:
        text = _html_to_text(_find_part(payload, "text/html"))
    text = _QUOTE_SPLIT_RE.split("\n" + text.replace("\r\n", "\n"), maxsplit=1)[0]
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def headers(message: Dict[str, Any]) -> Dict[str, str]:
    return {h["name"].lower(): h["value"] for h in (message.get("payload") or {}).get("headers", [])}


def says_confirmed(message: Dict[str, Any]) -> bool:
    """True when the word "Confirmed" is in what we wrote (not only in a quoted reply)."""
    return bool(_CONFIRMED_RE.search(new_text(message.get("payload") or {})))


def addresses(value: str) -> List[str]:
    return [a.lower() for a in re.findall(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", value or "")]


def customer_address(message: Dict[str, Any], company_addresses: List[str]) -> str:
    """First To/Cc address that is not the company's own (website form relay ignored)."""
    h = headers(message)
    own = {a.lower() for a in company_addresses}
    for addr in addresses(h.get("to", "")) + addresses(h.get("cc", "")):
        if addr not in own and not addr.startswith("wordpress@"):
            return addr
    return ""


def transcript(messages: List[Dict[str, Any]], company_addresses: List[str], upto_id: str = "") -> str:
    """Thread as plain text, oldest first, up to and including upto_id."""
    own = {a.lower() for a in company_addresses}
    parts: List[str] = []
    for message in messages:
        h = headers(message)
        sender = h.get("from", "")
        who = "JAPANESE REMOVALS (us)" if set(addresses(sender)) & own else "CUSTOMER"
        att = attachment_names(message.get("payload") or {})
        parts.append(
            "--- Message {n} | {who} | {date}\nFrom: {frm}\nTo: {to}\nSubject: {subj}{att}\n\n{body}".format(
                n=len(parts) + 1,
                who=who,
                date=h.get("date", ""),
                frm=sender,
                to=h.get("to", ""),
                subj=h.get("subject", ""),
                att="\nAttachments: " + ", ".join(att) if att else "",
                body=new_text(message.get("payload") or {})[:MAX_MESSAGE_CHARS],
            )
        )
        if upto_id and message.get("id") == upto_id:
            break
    return "\n\n".join(parts)


# --- Claude ---------------------------------------------------------------------------

_PROMPT = """You read an email thread between a Perth removals company (Japanese Removals, "us") and a customer.
The LAST message in the THREAD was sent by us and contains the word "confirmed". Today in Perth is {today}.

1. Classify what that last message confirms:
   "booking"      - it confirms a removal job (date and usually time) - also when it re-confirms one
   "amendment"    - it confirms a CHANGE to a job we had already confirmed (new date/time, extra job, crew change)
   "payment"      - it only confirms a payment was received
   "cancellation" - it confirms a cancellation
   "other"        - anything else (e.g. confirming a quote detail only)

2. Extract the booking details from the whole thread (later messages override earlier ones).
   EARLIER EMAILS (if given) are older threads with the same customer - use them only to fill details
   that the thread does not contradict.
   NEVER GUESS. If a value is not clearly stated, use null and list the field name in "missing".
   If a value is stated but ambiguous, only proposed and not agreed, or conflicting, fill the most likely
   reading and list it in "uncertain" with a short reason.
   Dates without a year: the next such date on or after the email date.

Return JSON only:
{{
  "kind": "booking|amendment|payment|cancellation|other",
  "kind_reason": "one short sentence",
  "summary": "one short line, e.g. 'Booking Tue 24 Nov 8am, 2 men, Karrinyup to Perth'",
  "customer_name": null,
  "email": null,
  "phone": null,
  "move_date": "YYYY-MM-DD or null",
  "start_time": "HH:MM 24h or null (for a window like 1pm-2pm use 13:00 and put the window in notes)",
  "pickup_address": null,
  "delivery_address": null,
  "num_movers": null,
  "hourly_rate": "number or null (the rate for the agreed team size)",
  "callout_fee": "call-out charge in dollars as a number, or null",
  "callout_minutes": "call-out given as time (e.g. 30 min call out -> 30), or null",
  "additional_charges": [{{"description": "", "amount": 0}}],
  "furniture": "items / volume as text, or null",
  "notes": "what the crew must know: access, stairs, lift, parking, packing, time window, payment method; or null",
  "missing": ["field names from the list above"],
  "uncertain": [{{"field": "", "reason": ""}}]
}}

THREAD:
{thread}
{context}"""


def _extract_json(text: str) -> Dict[str, Any]:
    body = (text or "").strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < 0:
        raise ParseError("The analysis did not return booking details.")
    try:
        data = json.loads(body[start : end + 1])
    except ValueError as exc:
        raise ParseError("The analysis result could not be read ({0}).".format(exc)) from exc
    if not isinstance(data, dict):
        raise ParseError("The analysis result could not be read.")
    return data


def ai_available() -> bool:
    return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())


def ask_claude(thread_text: str, context_text: str, today_iso: str) -> Dict[str, Any]:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise ParseError("ANTHROPIC_API_KEY is not set.")
    context = ""
    if context_text.strip():
        context = "\nEARLIER EMAILS WITH THIS CUSTOMER (older threads):\n" + context_text[:MAX_CONTEXT_CHARS]
    payload = {
        "model": (os.environ.get("GMAIL_IMPORT_MODEL") or DEFAULT_MODEL).strip(),
        "max_tokens": 2500,
        "messages": [
            {"role": "user", "content": _PROMPT.format(today=today_iso, thread=thread_text, context=context)}
        ],
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
        raise ParseError("The analysis service returned an error ({0}).".format(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ParseError("The analysis service could not be reached.") from exc
    text = "".join(p.get("text", "") for p in result.get("content", []) if p.get("type") == "text")
    return _extract_json(text)


# --- cleaning ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("null", "none", "n/a", "unknown") else text


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def _iso_date(value: Any) -> str:
    text = _text(value)
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else ""


def _hm(value: Any) -> str:
    text = _text(value)
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return ""
    return "{0:02d}:{1}".format(int(match.group(1)), match.group(2))


def clean(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise Claude's answer; invalid values become empty and are marked missing."""
    kind = _text(raw.get("kind")).lower()
    out: Dict[str, Any] = {
        "kind": kind if kind in KINDS else "other",
        "kind_reason": _text(raw.get("kind_reason")),
        "summary": _text(raw.get("summary")),
        "customer_name": _text(raw.get("customer_name")),
        "email": _text(raw.get("email")).lower(),
        "phone": _text(raw.get("phone")),
        "move_date": _iso_date(raw.get("move_date")),
        "start_time": _hm(raw.get("start_time")),
        "pickup_address": _text(raw.get("pickup_address")),
        "delivery_address": _text(raw.get("delivery_address")),
        "furniture": _text(raw.get("furniture")),
        "notes": _text(raw.get("notes")),
    }
    movers = _number(raw.get("num_movers"))
    out["num_movers"] = int(movers) if movers and 1 <= movers <= 20 else None
    rate = _number(raw.get("hourly_rate"))
    out["hourly_rate"] = rate if rate and rate > 0 else None
    fee = _number(raw.get("callout_fee"))
    out["callout_fee"] = fee if fee and fee > 0 else None
    minutes = _number(raw.get("callout_minutes"))
    out["callout_minutes"] = int(minutes) if minutes and 0 < minutes <= 240 else None
    charges = []
    for item in raw.get("additional_charges") or []:
        if not isinstance(item, dict):
            continue
        desc, amount = _text(item.get("description")), _number(item.get("amount"))
        if desc and amount:
            charges.append({"description": desc, "amount": amount})
    out["additional_charges"] = charges

    missing = {f for f in (raw.get("missing") or []) if isinstance(f, str) and f in FIELD_NAMES}
    for field in ("customer_name", "email", "phone", "move_date", "start_time", "pickup_address",
                  "delivery_address", "num_movers", "hourly_rate"):
        if out.get(field) in ("", None):
            missing.add(field)
        else:
            missing.discard(field)
    if out["callout_fee"] is None and out["callout_minutes"] is None:
        missing.add("callout_fee")
    else:
        missing.discard("callout_fee")
        missing.discard("callout_minutes")
    if out["furniture"]:
        missing.discard("furniture")
    out["missing"] = [f for f in FIELD_NAMES if f in missing]

    uncertain = []
    for item in raw.get("uncertain") or []:
        if isinstance(item, dict) and _text(item.get("field")) in FIELD_NAMES:
            uncertain.append({"field": _text(item.get("field")), "reason": _text(item.get("reason"))[:300]})
    out["uncertain"] = uncertain
    return out
