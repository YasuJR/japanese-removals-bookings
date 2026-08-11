"""Hourly rate defaults based on number of movers (create/edit forms only)."""

from __future__ import annotations

from typing import Dict, Optional

MOVER_HOURLY_RATES: Dict[int, float] = {
    2: 180.0,
    3: 235.0,
}
DEFAULT_CALLOUT_FEE = 90.0


def hourly_rate_for_movers(num_movers: int) -> Optional[float]:
    """Return the standard hourly rate for supported mover counts."""
    try:
        count = int(num_movers)
    except (TypeError, ValueError):
        return None
    return MOVER_HOURLY_RATES.get(count)


class MoverPricingState:
    """Track whether the hourly rate was manually overridden in the form."""

    def __init__(self) -> None:
        self.manual_override = False

    def apply_for_movers(self, num_movers: int, current_rate: float) -> float:
        if self.manual_override:
            return current_rate
        suggested = hourly_rate_for_movers(num_movers)
        if suggested is None:
            return current_rate
        return suggested

    def mark_manual_override(self) -> None:
        self.manual_override = True
