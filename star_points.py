"""Staff Portal Star Points — display helpers and edit permission checks."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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


def can_manage_star_points(user: Any) -> bool:
    """Owner/Admin may adjust stars; delegates to Staff Portal owner check."""
    import staff_portal_owner

    return staff_portal_owner.can_manage_staff_portal(user)


def build_star_points_history_views(events: Any) -> List[Dict[str, Any]]:
    """Format persisted events for Staff Portal display."""
    from display_dates import format_display_date

    views: List[Dict[str, Any]] = []
    for row in events or []:
        if isinstance(row, dict):
            event = row
        else:
            try:
                event = dict(row)
            except (TypeError, ValueError):
                continue
        created = str(event.get("created_at") or "").strip()
        date_label = created[:10]
        if len(created) >= 10:
            parts = format_display_date(created[:10])
            date_label = "{0} {1} {2}".format(
                int(parts.get("day") or created[8:10]),
                parts.get("month") or "",
                parts.get("year") or created[:4],
            )
        try:
            delta = int(event.get("points_delta") or 0)
        except (TypeError, ValueError):
            delta = 0
        change_type = str(event.get("change_type") or "").strip().lower()
        if change_type == "reset":
            delta_label = "Reset"
        elif delta > 0:
            delta_label = "+{0}".format(delta)
        elif delta < 0:
            delta_label = str(delta)
        else:
            delta_label = "0"
        reason = str(event.get("reason") or "").strip()
        views.append(
            {
                "date_label": date_label,
                "delta_label": delta_label,
                "reason": reason,
                "change_type": change_type,
            }
        )
    return views
