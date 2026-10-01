"""Truck assignment helpers."""

from typing import Any, Dict, List, Optional, Tuple

import database as db

TruckResourceKey = Tuple[str, Any]


def _normalize_truck_label(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def truck_resource_key(truck_assigned: Any) -> Optional[TruckResourceKey]:
    """Stable truck identity for conflict checks (DB id preferred)."""
    label = _normalize_truck_label(truck_assigned)
    if not label:
        return None
    for row in db.list_trucks(active_only=False):
        name = _normalize_truck_label(row.get("name"))
        if name.lower() == label.lower():
            return ("id", int(row["id"]))
    return ("name", label.lower())


def truck_resource_key_for_booking(booking: Dict[str, Any]) -> Optional[TruckResourceKey]:
    return truck_resource_key(booking.get("truck_assigned"))


def same_truck_resource(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """True when both bookings use the same truck resource."""
    key_a = truck_resource_key_for_booking(a)
    key_b = truck_resource_key_for_booking(b)
    if key_a is None or key_b is None:
        return False
    return key_a == key_b


def schedule_overlap_requires_override(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """
    Whether overlapping jobs should trigger double-booking override.

    Same truck → yes; different trucks → no; one unassigned → no;
    both unassigned → legacy time-slot overlap (Phase 14).
    """
    key_a = truck_resource_key_for_booking(a)
    key_b = truck_resource_key_for_booking(b)
    if key_a is not None and key_b is not None:
        return key_a == key_b
    if key_a is None and key_b is None:
        return True
    return False


def active_truck_names() -> List[str]:
    rows = db.list_trucks(active_only=True)
    return [row["name"] for row in rows]


def all_truck_names() -> List[str]:
    rows = db.list_trucks(active_only=False)
    return [row["name"] for row in rows]
