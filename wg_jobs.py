"""WG Outdoor Life (contract client) detection for the Dashboard Unpaid list.

Bookings have no company / client field, so WG jobs are identified by
customer_name. Production spellings (2026-10): "WG", "WG Outdoor Life",
"WG Outdoor life". Matching is word-based, never a raw substring, so names
like "Wgrace" or "Greg Wg Smith" are not WG jobs.
"""

import re
import unicodedata
from typing import Any

WG_SECTION_LABEL = "WG OUTDOOR LIFE"
# bookings.source for orders entered on the WG Orders page (wg_orders.py).
WG_ORDER_SOURCE = "wg_outdoor_life"

_WG_PHRASE_RE = re.compile(r"\bwg outdoor life\b")


def normalize_customer_name(name: Any) -> str:
    """Lowercase, NFKC, punctuation → spaces, collapsed whitespace."""
    text = unicodedata.normalize("NFKC", str(name or "")).lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return " ".join(text.split())


def is_wg_outdoor_life_name(name: Any) -> bool:
    """True when the first word is "WG" or the name contains "WG Outdoor Life"."""
    text = normalize_customer_name(name)
    if not text:
        return False
    return text.split()[0] == "wg" or bool(_WG_PHRASE_RE.search(text))


def _field(job: Any, key: str) -> Any:
    if isinstance(job, dict):
        return job.get(key)
    if hasattr(job, "keys"):
        try:
            return job[key]
        except (KeyError, TypeError, IndexError):
            return None
    return None


def is_wg_order(job: Any) -> bool:
    """True for bookings created from the WG Orders page (source = wg_outdoor_life)."""
    return str(_field(job, "source") or "").strip().lower() == WG_ORDER_SOURCE


def is_wg_day(job: Any) -> bool:
    """True for a WG Day booking (one per day; its delivery stops are in wg_stops)."""
    return is_wg_order(job) and _field(job, "wg_stop_count") not in (None, "")


def stops_label(count: Any) -> str:
    """"4 stops" / "1 stop"."""
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        n = 0
    return "{0} stop{1}".format(n, "" if n == 1 else "s")


def is_wg_outdoor_life(job: Any) -> bool:
    if is_wg_order(job):
        return True
    raw = None
    if isinstance(job, dict):
        raw = job.get("customer_name")
    elif hasattr(job, "keys"):
        try:
            raw = job["customer_name"]
        except (KeyError, TypeError, IndexError):
            raw = None
    return is_wg_outdoor_life_name(raw)
