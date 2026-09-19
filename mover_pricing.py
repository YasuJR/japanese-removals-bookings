"""Hourly rate and callout defaults based on number of movers (create/edit forms only)."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

MOVER_HOURLY_RATES: Dict[int, float] = {
    2: 180.0,
    3: 235.0,
}
MOVER_CALLOUT_MINUTES: Dict[int, int] = {
    2: 30,
    3: 30,
}
DEFAULT_CALLOUT_FEE = 90.0


def hourly_rate_for_movers(num_movers: int) -> Optional[float]:
    """Return the standard hourly rate for supported mover counts."""
    try:
        count = int(num_movers)
    except (TypeError, ValueError):
        return None
    return MOVER_HOURLY_RATES.get(count)


def callout_minutes_for_movers(num_movers: int) -> Optional[int]:
    try:
        count = int(num_movers)
    except (TypeError, ValueError):
        return None
    return MOVER_CALLOUT_MINUTES.get(count)


def callout_fee_for_movers(num_movers: int) -> Optional[float]:
    """Return standard callout fee from mover count (30 min × hourly rate)."""
    import callout_pricing

    hourly = hourly_rate_for_movers(num_movers)
    minutes = callout_minutes_for_movers(num_movers)
    if hourly is None or minutes is None:
        return None
    return callout_pricing.fee_amount(hourly, minutes)


def pricing_for_movers(num_movers: int) -> Optional[Tuple[float, float]]:
    hourly = hourly_rate_for_movers(num_movers)
    callout = callout_fee_for_movers(num_movers)
    if hourly is None or callout is None:
        return None
    return hourly, callout


class MoverPricingState:
    """Track manual overrides for hourly rate and callout fee in the form."""

    def __init__(self) -> None:
        self.manual_hourly_override = False
        self.manual_callout_override = False
        self.manual_callout_minutes_override = False

    def apply_hourly_for_movers(self, num_movers: int, current_rate: float) -> float:
        if self.manual_hourly_override:
            return current_rate
        suggested = hourly_rate_for_movers(num_movers)
        if suggested is None:
            return current_rate
        return suggested

    def apply_callout_minutes_for_movers(
        self, num_movers: int, current_minutes: int
    ) -> int:
        if self.manual_callout_minutes_override:
            return current_minutes
        suggested = callout_minutes_for_movers(num_movers)
        if suggested is None:
            return current_minutes
        return suggested

    def apply_callout_for_movers(self, num_movers: int, current_callout: float) -> float:
        if self.manual_callout_override:
            return current_callout
        suggested = callout_fee_for_movers(num_movers)
        if suggested is None:
            return current_callout
        return suggested

    def apply_for_movers(self, num_movers: int, current_rate: float) -> float:
        return self.apply_hourly_for_movers(num_movers, current_rate)

    def mark_manual_override(self) -> None:
        self.manual_hourly_override = True

    def mark_manual_hourly_override(self) -> None:
        self.manual_hourly_override = True

    def mark_manual_callout_override(self) -> None:
        self.manual_callout_override = True

    def mark_manual_callout_minutes_override(self) -> None:
        self.manual_callout_minutes_override = True
