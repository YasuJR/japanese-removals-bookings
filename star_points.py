"""Staff Portal Star Rewards — display helpers and edit permission checks.

Small Stars (crew_members.star_points) count 0–9. The 10th Small Star turns into a
Big Star (one row in crew_big_stars, with when / who / why) and Small Stars go back
to 0. Big Stars have no upper limit and are only removed by an explicit owner action.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

SMALL_STARS_PER_BIG = 10
STAR_POINTS_MAX = SMALL_STARS_PER_BIG  # legacy name
BIG_STAR = "🌟"
FILLED_STAR = "★"
EMPTY_STAR = "☆"
BIG_STAR_ICONS_SHOWN = 12  # more than this shows the number only


def clamp_small_stars(value: Any) -> int:
    try:
        points = int(value)
    except (TypeError, ValueError):
        points = 0
    return max(0, min(SMALL_STARS_PER_BIG - 1, points))


clamp_star_points = clamp_small_stars  # legacy name


def progress_display(small: int) -> str:
    small = clamp_small_stars(small)
    return (FILLED_STAR * small) + (EMPTY_STAR * (SMALL_STARS_PER_BIG - small))


def build_star_rewards_view(small: Any, big: Any = 0) -> Dict[str, Any]:
    """Read-only payload for templates (no DB access)."""
    small = clamp_small_stars(small)
    try:
        big = max(0, int(big or 0))
    except (TypeError, ValueError):
        big = 0
    return {
        "points": small,
        "small": small,
        "big": big,
        "max_points": SMALL_STARS_PER_BIG,
        "count_display": "{0} / {1}".format(small, SMALL_STARS_PER_BIG),
        "stars_display": progress_display(small),
        "big_icons": BIG_STAR * min(big, BIG_STAR_ICONS_SHOWN),
        "big_more": big > BIG_STAR_ICONS_SHOWN,
        "to_next": SMALL_STARS_PER_BIG - small,
    }


def build_star_points_view(points: Any, big: Any = 0) -> Dict[str, Any]:
    return build_star_rewards_view(points, big)


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
        if change_type == "milestone":
            delta_label = "{0} Big Star".format(BIG_STAR)
        elif change_type == "big_star_removed":
            delta_label = "−1 Big Star"
        elif change_type == "migration":
            delta_label = "Converted"
        elif change_type in ("reset", "reset_small"):
            delta_label = "Reset small"
        elif change_type == "reset_all":
            delta_label = "Reset all"
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
                "milestone": change_type == "milestone",
                "big_stars_after": event.get("big_stars_after"),
            }
        )
    return views
