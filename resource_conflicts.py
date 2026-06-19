"""Phase 19 — crew and truck double-booking detection."""

from typing import Any, Dict, List, Optional

import database as db
import double_booking
import job_status
from booking_times import display_finish_time, display_start_time
from crew import crew_from_storage


def _candidate_booking(
    booking: Dict[str, Any],
    exclude_booking_id: Optional[int] = None,
) -> Dict[str, Any]:
    candidate = dict(booking)
    if exclude_booking_id is not None:
        candidate["id"] = exclude_booking_id
    elif "id" not in candidate:
        candidate["id"] = -1
    return candidate


def _times_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    a_start, a_end = double_booking.booking_event_datetimes(a)
    b_start, b_end = double_booking.booking_event_datetimes(b)
    return a_start < b_end and b_start < a_end


def _should_check_booking(booking: Dict[str, Any]) -> bool:
    status = job_status.normalize(booking.get("status"))
    if status == "Cancelled":
        return False
    return double_booking.blocks_availability(status)


def find_crew_conflict_warnings(
    booking: Dict[str, Any],
    exclude_booking_id: Optional[int] = None,
) -> List[str]:
    """Return warnings when assigned crew overlap another blocking job."""
    crew = crew_from_storage(booking.get("crew"))
    if not crew:
        return []

    move_date = (booking.get("move_date") or "").strip()
    if not move_date:
        return []

    if not _should_check_booking(booking):
        return []

    candidate = _candidate_booking(booking, exclude_booking_id)
    warnings: List[str] = []

    for row in db.list_by_date(move_date):
        other = dict(row)
        other_id = int(other["id"])
        if exclude_booking_id is not None and other_id == exclude_booking_id:
            continue
        if int(candidate.get("id", -1)) == other_id:
            continue
        if not _should_check_booking(other):
            continue
        if not _times_overlap(candidate, other):
            continue

        other_crew = crew_from_storage(other.get("crew"))
        shared = [name for name in crew if name in other_crew]
        if not shared:
            continue

        slot = "{0} - {1}".format(
            display_start_time(other),
            display_finish_time(other),
        )
        customer = other.get("customer_name") or "another job"
        for name in shared:
            warnings.append(
                "WARNING: {0} already assigned {1} (#{2} {3}).".format(
                    name, slot, other_id, customer
                )
            )
    return warnings


def find_truck_conflict_warnings(
    booking: Dict[str, Any],
    exclude_booking_id: Optional[int] = None,
) -> List[str]:
    """Return warnings when assigned truck overlaps another blocking job."""
    truck = (booking.get("truck_assigned") or "").strip()
    if not truck:
        return []

    move_date = (booking.get("move_date") or "").strip()
    if not move_date:
        return []

    if not _should_check_booking(booking):
        return []

    candidate = _candidate_booking(booking, exclude_booking_id)
    warnings: List[str] = []

    for row in db.list_by_date(move_date):
        other = dict(row)
        other_id = int(other["id"])
        if exclude_booking_id is not None and other_id == exclude_booking_id:
            continue
        if int(candidate.get("id", -1)) == other_id:
            continue
        if not _should_check_booking(other):
            continue
        if not _times_overlap(candidate, other):
            continue

        other_truck = (other.get("truck_assigned") or "").strip()
        if not other_truck or other_truck.lower() != truck.lower():
            continue

        slot = "{0} - {1}".format(
            display_start_time(other),
            display_finish_time(other),
        )
        customer = other.get("customer_name") or "another job"
        warnings.append(
            "WARNING: {0} already assigned {1} (#{2} {3}).".format(
                truck, slot, other_id, customer
            )
        )
    return warnings


def find_resource_conflict_warnings(
    booking: Dict[str, Any],
    exclude_booking_id: Optional[int] = None,
) -> List[str]:
    return find_crew_conflict_warnings(
        booking, exclude_booking_id=exclude_booking_id
    ) + find_truck_conflict_warnings(booking, exclude_booking_id=exclude_booking_id)


def has_crew_conflict(
    booking: Dict[str, Any],
    exclude_booking_id: Optional[int] = None,
) -> bool:
    return bool(find_crew_conflict_warnings(booking, exclude_booking_id=exclude_booking_id))


def has_truck_conflict(
    booking: Dict[str, Any],
    exclude_booking_id: Optional[int] = None,
) -> bool:
    return bool(find_truck_conflict_warnings(booking, exclude_booking_id=exclude_booking_id))
