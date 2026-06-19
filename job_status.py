"""Job status values, sorting, and display helpers."""

from typing import Any, Dict, List, Optional

DEFAULT_STATUS = "Quote"

OPTIONS: List[str] = [
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


def normalize(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in OPTIONS else DEFAULT_STATUS


def sort_priority(status: Any) -> int:
    return SORT_PRIORITY.get(normalize(status), 50)


def css_class(status: Any) -> str:
    return CSS_CLASS.get(normalize(status), "quote")


def display(booking: Dict[str, Any]) -> str:
    return normalize(booking.get("status"))


def validate(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if text in OPTIONS:
        return text
    return None
