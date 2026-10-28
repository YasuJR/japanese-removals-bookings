"""Customer billing work periods (Booking Edit / Invoice).

Period 1 is the booking itself: start_time, finish_time, num_movers, hourly_rate.
Period 2 is optional, for jobs where the number of movers changed part-way:
bookings.work_period_2_start / _finish / _movers / _rate. Existing bookings have
these empty, so they stay single-period and their invoices are unchanged.

Work periods are for the customer's invoice only. Staff Paid Hours still come from
staff_job_times (booking times, actual times and Staff Portal individual overrides).
The call-out fee is charged once per job, never once per period.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import mover_pricing
from booking_times import duration_hours_from_times, normalize_time_input

FIELDS = ("work_period_2_start", "work_period_2_finish", "work_period_2_movers", "work_period_2_rate")
FORM_FLAG = "work_period_2_enabled"
MAX_MOVERS = 20


def empty_fields() -> Dict[str, Any]:
    return {
        "work_period_2_start": "",
        "work_period_2_finish": "",
        "work_period_2_movers": None,
        "work_period_2_rate": None,
    }


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    try:
        text = str(value).strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def period_two(booking: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The saved Period 2, or None when the booking has only Period 1."""
    start = normalize_time_input(booking.get("work_period_2_start"))
    finish = normalize_time_input(booking.get("work_period_2_finish"))
    movers = _int(booking.get("work_period_2_movers"))
    if not start or not finish or not movers or movers < 1:
        return None
    hours = duration_hours_from_times(start, finish)
    if hours is None:
        return None
    rate = _float(booking.get("work_period_2_rate"))
    if rate is None:
        rate = mover_pricing.hourly_rate_for_movers(movers)
    if rate is None:
        return None
    return {"start": start, "finish": finish, "movers": movers, "rate": float(rate), "hours": hours}


def has_period_two(booking: Dict[str, Any]) -> bool:
    return period_two(booking) is not None


def parse_form(form: Any, *, start_time: str = "", finish_time: str = "") -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    Period 2 fields from the Booking Edit form.

    Returns (None, []) when the form has no work-period section (e.g. New Booking or
    other forms), so saving never clears a Period 2 it didn't show. With the section
    present but switched off, returns empty fields (Period 2 removed).
    """
    if FORM_FLAG not in form:
        return None, []
    if str(form.get(FORM_FLAG) or "").strip() != "1":
        return empty_fields(), []

    errors: List[str] = []
    start = normalize_time_input(form.get("work_period_2_start"))
    finish = normalize_time_input(form.get("work_period_2_finish"))
    movers = _int(form.get("work_period_2_movers"))
    rate_raw = str(form.get("work_period_2_rate") or "").strip()

    if not start or not finish:
        errors.append("Additional work period: enter both start and finish time.")
    elif duration_hours_from_times(start, finish) is None:
        errors.append("Additional work period: finish time must be after start time.")
    if movers is None or movers < 1 or movers > MAX_MOVERS:
        errors.append("Additional work period: number of movers must be 1–{0}.".format(MAX_MOVERS))
        movers = None

    rate: Optional[float] = None
    if rate_raw:
        rate = _float(rate_raw)
        if rate is None or rate < 0:
            errors.append("Additional work period: hourly rate must be a non-negative number.")
            rate = None
    elif movers:
        # Same standard rate table as Period 1 (mover_pricing); nothing hard-coded here.
        rate = mover_pricing.hourly_rate_for_movers(movers)
        if rate is None:
            errors.append(
                "Additional work period: there is no standard rate for {0} movers — enter the hourly rate.".format(movers)
            )

    p1_finish = normalize_time_input(finish_time)
    p1_start = normalize_time_input(start_time)
    if start and p1_finish and p1_start and not errors:
        if duration_hours_from_times(p1_start, p1_finish) is not None and duration_hours_from_times(start, p1_finish) is not None:
            errors.append("Additional work period must start at or after the Period 1 finish time (no overlap).")

    return {
        "work_period_2_start": start,
        "work_period_2_finish": finish,
        "work_period_2_movers": movers,
        "work_period_2_rate": rate,
    }, errors


def labour_lines(booking: Dict[str, Any], *, hourly_rate: float, hours: float) -> List[Dict[str, Any]]:
    """
    One labour line per work period. Period 1 uses the booking's hourly rate and
    invoice hours (start→finish), exactly as before; Period 2 its own movers/rate.
    """
    lines = [
        {
            "period": 1,
            "start": normalize_time_input(booking.get("start_time")),
            "finish": normalize_time_input(booking.get("finish_time")),
            "movers": _int(booking.get("num_movers")),
            "hours": float(hours),
            "rate": float(hourly_rate),
            "amount": round(float(hourly_rate) * float(hours), 2),
        }
    ]
    second = period_two(booking)
    if second:
        lines.append(
            {
                "period": 2,
                "start": second["start"],
                "finish": second["finish"],
                "movers": second["movers"],
                "hours": second["hours"],
                "rate": second["rate"],
                "amount": round(second["rate"] * second["hours"], 2),
            }
        )
    return lines
