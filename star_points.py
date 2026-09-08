"""Staff Portal Star Points — display helpers and edit permission checks."""

from __future__ import annotations

from typing import Any, Dict, Optional

STAR_POINTS_MAX = 10
FILLED_STAR = "⭐"
EMPTY_STAR = "☆"


def clamp_star_points(value: Any) -> int:
    try:
        points = int(value)
    except (TypeError, ValueError):
        points = 0
    return max(0, min(STAR_POINTS_MAX, points))


def build_star_points_view(points: Any) -> Dict[str, Any]:
    """Read-only payload for templates (no DB access)."""
    points = clamp_star_points(points)
    return {
        "points": points,
        "max_points": STAR_POINTS_MAX,
        "count_display": "{0} / {1}".format(points, STAR_POINTS_MAX),
        "stars_display": (FILLED_STAR * points) + (EMPTY_STAR * (STAR_POINTS_MAX - points)),
        "bonus_ready": points >= STAR_POINTS_MAX,
    }


def can_manage_star_points(admin_user_id: Optional[Any]) -> bool:
    """Owner/Admin may adjust stars; staff viewers may not (future per-staff login)."""
    return admin_user_id is not None
