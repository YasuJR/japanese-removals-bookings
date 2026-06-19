"""Phase 14 — double booking detection (warning + optional override)."""

from datetime import date, datetime, time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import automation
import config
import database as db
import job_status
from booking_times import (
    DEFAULT_DURATION_HOURS,
    DEFAULT_START_TIME,
    display_start_time,
    effective_start_hm,
    finish_from_duration,
    format_time_12h,
    parse_duration_hours,
)

BLOCKING_STATUSES = frozenset({"Confirmed", "On Route", "Paid", "Completed"})
IGNORE_STATUSES = frozenset({"Pending", "Cancelled"})

OVERRIDE_FORM_FIELD = "double_booking_override_confirm"


def blocks_availability(status: Any) -> bool:
    return job_status.normalize(status) in BLOCKING_STATUSES


def should_ignore_status(status: Any) -> bool:
    normalized = job_status.normalize(status)
    return normalized in IGNORE_STATUSES or not blocks_availability(normalized)


def _effective_finish_hm(booking: Dict[str, Any]) -> str:
    duration = parse_duration_hours(booking.get("duration_hours"))
    start_hm = effective_start_hm(booking)
    if duration is not None:
        return finish_from_duration(start_hm, duration)
    stored = (booking.get("finish_time") or "").strip()
    if stored:
        return stored
    return finish_from_duration(start_hm, DEFAULT_DURATION_HOURS)


def booking_event_datetimes(booking: Dict[str, Any]) -> Tuple[datetime, datetime]:
    tz = ZoneInfo(config.TIMEZONE)
    move_day = datetime.strptime(booking["move_date"], "%Y-%m-%d").date()
    start_hm = effective_start_hm(booking)
    finish_hm = _effective_finish_hm(booking)
    start_parts = start_hm.split(":")
    finish_parts = finish_hm.split(":")
    start_t = time(int(start_parts[0]), int(start_parts[1]))
    finish_t = time(int(finish_parts[0]), int(finish_parts[1]))
    return (
        datetime.combine(move_day, start_t, tzinfo=tz),
        datetime.combine(move_day, finish_t, tzinfo=tz),
    )


def _times_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    a_start, a_end = booking_event_datetimes(a)
    b_start, b_end = booking_event_datetimes(b)
    return a_start < b_end and b_start < a_end


def find_conflicts(
    booking: Dict[str, Any],
    exclude_booking_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return blocking bookings on the same date that overlap in time."""
    move_date = (booking.get("move_date") or "").strip()
    if not move_date:
        return []

    candidate = dict(booking)
    if exclude_booking_id is not None:
        candidate["id"] = exclude_booking_id
    elif "id" not in candidate:
        candidate["id"] = -1

    conflicts: List[Dict[str, Any]] = []
    for row in db.list_by_date(move_date):
        other = dict(row)
        other_id = int(other["id"])
        if exclude_booking_id is not None and other_id == exclude_booking_id:
            continue
        if int(candidate.get("id", -1)) == other_id:
            continue
        if should_ignore_status(other.get("status")):
            continue
        if not _times_overlap(candidate, other):
            continue
        conflicts.append(
            {
                "id": other_id,
                "customer_name": other.get("customer_name") or "—",
                "status": job_status.display(other),
                "start_time": display_start_time(other),
                "finish_time": display_finish_hm(other),
                "time_label": "{0} – {1}".format(
                    display_start_time(other),
                    display_finish_hm(other),
                ),
            }
        )
    return conflicts


def display_finish_hm(booking: Dict[str, Any]) -> str:
    return format_time_12h(_effective_finish_hm(booking))


def booking_payload_from_form(
    data: Dict[str, Any],
    booking_id: Optional[int] = None,
) -> Dict[str, Any]:
    payload = {
        "id": booking_id if booking_id is not None else -1,
        "move_date": data.get("move_date") or "",
        "start_time": data.get("start_time") or DEFAULT_START_TIME,
        "finish_time": data.get("finish_time") or "",
        "duration_hours": data.get("duration_hours") or "",
        "status": data.get("status") or job_status.DEFAULT_STATUS,
        "customer_name": data.get("customer_name") or "",
    }
    return payload


def badge_for_booking(booking: Dict[str, Any]) -> Optional[str]:
    """
    Return badge key: conflict | clear | override, or None when not applicable.
    Pending bookings never show availability badges.
    """
    status = job_status.display(booking)
    if status == "Pending":
        return None
    if not blocks_availability(status):
        return None
    if (booking.get("double_booking_override_at") or "").strip():
        return "override"
    conflicts = find_conflicts(booking, exclude_booking_id=int(booking["id"]))
    if conflicts:
        return "conflict"
    return "clear"


def ui_context(
    booking: Dict[str, Any],
    form_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Template context for banners and badges on booking pages."""
    status = job_status.display(booking)
    booking_id = booking.get("id")
    payload = booking_payload_from_form(form_data or booking, booking_id)

    if status == "Pending":
        return {
            "double_booking_conflicts": [],
            "double_booking_badge": None,
            "show_double_booking_banner": False,
            "requires_double_booking_override": False,
        }

    if not blocks_availability(status):
        return {
            "double_booking_conflicts": [],
            "double_booking_badge": None,
            "show_double_booking_banner": False,
            "requires_double_booking_override": False,
        }

    conflicts = find_conflicts(
        payload,
        exclude_booking_id=int(booking_id) if booking_id else None,
    )
    badge = badge_for_booking({**booking, **payload})
    has_override = bool((booking.get("double_booking_override_at") or "").strip())
    return {
        "double_booking_conflicts": conflicts,
        "double_booking_badge": badge,
        "show_double_booking_banner": bool(conflicts) and not has_override,
        "requires_double_booking_override": bool(conflicts) and not has_override,
    }


def _log_checked(booking_id: Optional[int], conflict_count: int) -> None:
    automation.log_event(
        automation.AUTOMATION_DOUBLE_BOOKING_CHECKED,
        automation.STATUS_SUCCESS,
        "Checked {0} conflict(s).".format(conflict_count),
        booking_id=booking_id,
    )


def _log_conflict(booking_id: Optional[int], conflicts: List[Dict[str, Any]]) -> None:
    if not conflicts:
        return
    parts = [
        "#{0} {1} ({2}, {3})".format(
            c["id"],
            c["customer_name"],
            c["time_label"],
            c["status"],
        )
        for c in conflicts[:3]
    ]
    automation.log_event(
        automation.AUTOMATION_DOUBLE_BOOKING_CONFLICT,
        automation.STATUS_PARTIAL,
        "Conflict with: {0}".format("; ".join(parts)),
        booking_id=booking_id,
    )


def _log_override(booking_id: Optional[int]) -> None:
    automation.log_event(
        automation.AUTOMATION_DOUBLE_BOOKING_OVERRIDE,
        automation.STATUS_SUCCESS,
        "User confirmed double booking override.",
        booking_id=booking_id,
    )


def validate_save(
    data: Dict[str, Any],
    *,
    booking_id: Optional[int] = None,
    override_confirmed: bool = False,
) -> Tuple[List[str], List[Dict[str, Any]], bool]:
    """
    Validate double booking rules on save.

    Returns (errors, conflicts, override_recorded).
    Pending bookings skip checks entirely.
    """
    new_status = job_status.display(data)
    if new_status == "Pending":
        return [], [], False

    if not blocks_availability(new_status):
        return [], [], False

    payload = booking_payload_from_form(data, booking_id)
    conflicts = find_conflicts(payload, exclude_booking_id=booking_id)
    _log_checked(booking_id, len(conflicts))

    if not conflicts:
        return [], [], False

    _log_conflict(booking_id, conflicts)

    if override_confirmed:
        if booking_id:
            db.update_booking_integration_fields(
                booking_id,
                {
                    "double_booking_override_at": datetime.utcnow().isoformat(
                        timespec="seconds"
                    )
                },
            )
        _log_override(booking_id)
        return [], conflicts, True

    return [
        'Time overlap with another booking — tick "I understand this may be double booked" to save.'
    ], conflicts, False
