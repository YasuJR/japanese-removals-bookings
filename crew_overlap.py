"""Detect crew double-bookings on the same day (warning only)."""

from typing import Any, Dict, List, Optional

from resource_conflicts import find_crew_conflict_warnings


def find_crew_overlap_warnings(
    booking: Dict[str, Any],
    exclude_booking_id: Optional[int] = None,
) -> List[str]:
    """Return warning strings when assigned crew overlap another job."""
    return find_crew_conflict_warnings(booking, exclude_booking_id=exclude_booking_id)
