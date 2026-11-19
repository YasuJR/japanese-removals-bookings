"""Job status values, sorting, and display helpers."""

from typing import Any, Dict, List, Optional

DEFAULT_STATUS = "Quote"

# Created automatically from a sent "Confirmed" email (integrations/company_gmail.py).
# Not shown to staff, not synced to Google Calendar and no customer SMS until the
# owner presses Confirm Booking.
CHECK_TO_CONFIRM = "Check to Confirm"

OPTIONS: List[str] = [
    CHECK_TO_CONFIRM,
    "Pending",
    "Quote",
    "Confirmed",
    "On Route",
    "In Progress",
    "Completed",
    "Invoiced",
    "Paid",
    "Cancelled",
]

# Lower = earlier in dashboard sort (upcoming group first).
SORT_PRIORITY = {
    CHECK_TO_CONFIRM: 1,
    "Pending": 5,
    "Quote": 10,
    "Confirmed": 10,
    "On Route": 15,
    "In Progress": 20,
    "Completed": 30,
    "Invoiced": 35,
    "Paid": 40,
    "Cancelled": 90,
}

CSS_CLASS = {
    CHECK_TO_CONFIRM: "check-to-confirm",
    "Pending": "pending",
    "Quote": "quote",
    "Confirmed": "confirmed",
    "On Route": "on-route",
    "In Progress": "in-progress",
    "Completed": "completed",
    "Invoiced": "invoiced",
    "Paid": "paid",
    "Cancelled": "cancelled",
}

DASHBOARD_FILTERS = [
    ("all", "All"),
    ("today", "Today"),
    ("upcoming", "Upcoming"),
    ("completed", "Completed"),
    ("paid", "Paid"),
    ("cancelled", "Cancelled"),
]

# Quick status changes from the Dashboard jobs table.
DASHBOARD_INLINE_STATUS_OPTIONS: List[str] = [
    "Quote",
    "Confirmed",
    "Invoiced",
    "Completed",
    "Cancelled",
]


def normalize(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in OPTIONS else DEFAULT_STATUS


def sort_priority(status: Any) -> int:
    return SORT_PRIORITY.get(normalize(status), 50)


def css_class(status: Any) -> str:
    return CSS_CLASS.get(normalize(status), "quote")


def display(booking: Dict[str, Any]) -> str:
    return normalize(booking.get("status"))


def is_check_to_confirm(value: Any) -> bool:
    """True for a booking dict or a status string that is Check to Confirm."""
    if isinstance(value, str) or value is None:
        status = value
    else:
        try:
            status = value["status"]
        except (KeyError, IndexError, TypeError):
            status = ""
    return str(status or "").strip() == CHECK_TO_CONFIRM


def validate(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if text in OPTIONS:
        return text
    return None


def validate_dashboard_inline(value: Any) -> Optional[str]:
    text = validate(value)
    if text and text in DASHBOARD_INLINE_STATUS_OPTIONS:
        return text
    return None
