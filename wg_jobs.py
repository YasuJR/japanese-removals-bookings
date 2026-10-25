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


def is_wg_outdoor_life(job: Any) -> bool:
    raw = None
    if isinstance(job, dict):
        raw = job.get("customer_name")
    elif hasattr(job, "keys"):
        try:
            raw = job["customer_name"]
        except (KeyError, TypeError, IndexError):
            raw = None
    return is_wg_outdoor_life_name(raw)
