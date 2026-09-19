"""Call-out time (minutes) and fee calculations shared across booking, invoice, and staff."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

CALLOUT_MINUTE_PRESETS: Tuple[int, ...] = (0, 30, 45, 60, 90)
DEFAULT_CALLOUT_MINUTES = 30


def fee_amount(hourly_rate: Any, callout_minutes: Any) -> float:
    try:
        rate = float(hourly_rate or 0)
        minutes = int(callout_minutes or 0)
    except (TypeError, ValueError):
        return 0.0
    if rate <= 0 or minutes <= 0:
        return 0.0
    return round(rate * (minutes / 60.0), 2)


def hours_from_minutes(callout_minutes: Any) -> Optional[float]:
    try:
        minutes = int(callout_minutes or 0)
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    return round(minutes / 60.0, 2)


def stored_callout_minutes(booking: Dict[str, Any]) -> Optional[int]:
    if "callout_minutes" not in booking:
        return None
    raw = booking.get("callout_minutes")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def infer_minutes_from_legacy(hourly_rate: Any, callout_fee: Any) -> Optional[int]:
    """Backfill only when fee matches hourly_rate × minutes exactly."""
    try:
        rate = float(hourly_rate or 0)
        fee = float(callout_fee or 0)
    except (TypeError, ValueError):
        return None
    if fee <= 0:
        return 0
    if rate <= 0:
        return None
    minutes = int(round((fee / rate) * 60.0))
    if minutes <= 0:
        return None
    if abs(fee_amount(rate, minutes) - round(fee, 2)) <= 0.01:
        return minutes
    return None


def minutes_for_booking(booking: Dict[str, Any]) -> Optional[int]:
    stored = stored_callout_minutes(booking)
    if stored is not None:
        return stored
    return infer_minutes_from_legacy(
        booking.get("hourly_rate"), booking.get("callout_fee")
    )


def callout_hours(booking: Dict[str, Any]) -> Optional[float]:
    minutes = minutes_for_booking(booking)
    if minutes is not None:
        return hours_from_minutes(minutes)
    try:
        fee = float(booking.get("callout_fee") or 0)
        rate = float(booking.get("hourly_rate") or 0)
    except (TypeError, ValueError):
        return None
    if fee <= 0 or rate <= 0:
        return None
    hours = round(fee / rate, 2)
    return hours if hours > 0 else None


def format_minutes_label(minutes: Optional[int]) -> str:
    if minutes is None or minutes <= 0:
        return ""
    return "{0} min".format(int(minutes))


def staff_callout_hours_label(
    booking: Dict[str, Any], *, format_hours_short: Any
) -> str:
    hours = callout_hours(booking)
    if not hours:
        return ""
    minutes = minutes_for_booking(booking)
    base = format_hours_short(hours)
    if minutes and minutes > 0:
        return "+ {0} ({1})".format(base, format_minutes_label(minutes))
    return "+ {0} call out".format(base)


def daily_jobs_callout_label(hours: Optional[float]) -> str:
    if not hours or hours <= 0:
        return ""
    from staff_job_times import format_hours_short

    return "+ {0} call out".format(format_hours_short(hours))


def parse_callout_minutes_form(form: Any) -> Tuple[Optional[int], List[str]]:
    raw = (form.get("callout_minutes") if hasattr(form, "get") else "") or ""
    raw = str(raw).strip()
    if not raw:
        return 0, []
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return None, ["Call out time must be whole minutes (e.g. 30, 45, 60)."]
    if minutes < 0:
        return None, ["Call out time cannot be negative."]
    if minutes > 24 * 60:
        return None, ["Call out time is too large."]
    return minutes, []


def resolve_callout_fee(
    hourly_rate: Any,
    callout_minutes: int,
    *,
    override_fee: Any = None,
) -> float:
    override = str(override_fee or "").strip()
    if override:
        try:
            value = float(override)
        except (TypeError, ValueError):
            return fee_amount(hourly_rate, callout_minutes)
        if value < 0:
            return fee_amount(hourly_rate, callout_minutes)
        return round(value, 2)
    return fee_amount(hourly_rate, callout_minutes)


def callout_line_description(minutes: Optional[int]) -> str:
    label = format_minutes_label(minutes)
    if label:
        return "Callout fee ({0})".format(label)
    return "Callout fee"
