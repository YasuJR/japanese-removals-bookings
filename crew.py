"""Crew assignment for bookings."""

from typing import Any, Dict, List, Optional, Sequence

import database as db
from integrations import company_config

CREW_OPTIONS = ["Yasu", "Tom", "Ken"]

# Google Calendar colorId (Calendar API v3)
CALENDAR_COLOR_YASU = "9"  # blue
CALENDAR_COLOR_TOM = "10"  # green
CALENDAR_COLOR_KEN = "6"  # orange
CALENDAR_COLOR_MULTIPLE = "3"  # purple

CALENDAR_COLOR_BY_MEMBER = {
    "Yasu": CALENDAR_COLOR_YASU,
    "Tom": CALENDAR_COLOR_TOM,
    "Ken": CALENDAR_COLOR_KEN,
}


def active_crew_names() -> List[str]:
    rows = db.list_crew_members(active_only=True)
    if rows:
        return [row["name"] for row in rows]
    return company_config.crew_options() or CREW_OPTIONS


def all_crew_names() -> List[str]:
    rows = db.list_crew_members(active_only=False)
    if rows:
        return [row["name"] for row in rows]
    return company_config.crew_options() or CREW_OPTIONS


def parse_crew_from_form(form: Any) -> List[str]:
    """Read validated crew checkboxes from a Flask form."""
    if hasattr(form, "getlist"):
        selected = form.getlist("crew")
    else:
        raw = form.get("crew", [])
        selected = raw if isinstance(raw, list) else [raw] if raw else []
    allowed = active_crew_names()
    return [name for name in selected if name in allowed]


def crew_storage_value(names: Sequence[str]) -> str:
    return ",".join(names)


def crew_from_storage(value: Any) -> List[str]:
    text = str(value or "").strip()
    if not text:
        return []
    known = set(all_crew_names())
    return [
        name
        for name in (part.strip() for part in text.split(","))
        if name and (not known or name in known)
    ]


def display_crew(booking: Dict[str, Any]) -> str:
    names = crew_from_storage(booking.get("crew"))
    return ", ".join(names) if names else "—"


def calendar_color_id(booking: Dict[str, Any]) -> Optional[str]:
    """Google Calendar colour from crew assignment."""
    names = crew_from_storage(booking.get("crew"))
    if not names:
        return None
    if len(names) > 1:
        return CALENDAR_COLOR_MULTIPLE
    return CALENDAR_COLOR_BY_MEMBER.get(names[0])
