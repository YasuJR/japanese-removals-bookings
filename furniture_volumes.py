"""Standard furniture volumes (m³) that the office admin can change.

The researched default for each item comes from furniture_volume_refs.py. An
admin override (table ``furniture_item_volumes``) replaces it for items added to
lists from then on; items already in a list keep the m³ they were added with
(furniture_catalog.freeze_units). Every change is logged in
``furniture_item_volume_log``.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import database as db
import furniture_catalog as catalog
import furniture_volume_refs as refs

MIN_M3 = 0.01
MAX_M3 = catalog.MAX_UNIT_M3
MAX_NOTE = 300


class VolumeError(ValueError):
    pass


def _now() -> str:
    # Same format as furniture_lists.now_iso so format_perth can show it.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def overrides() -> Dict[str, Dict[str, Any]]:
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT item_key, m3, note, updated_at, updated_by FROM furniture_item_volumes"
        ).fetchall()
    result = {}
    for row in rows:
        item = dict(row)
        if item["item_key"] in catalog.ITEMS:
            item["m3"] = float(item["m3"])
            result[item["item_key"]] = item
    return result


def effective_volumes() -> Dict[str, float]:
    """Current standard m³ for every item: admin override, else researched default."""
    volumes = catalog.default_volumes()
    try:
        for key, row in overrides().items():
            volumes[key] = row["m3"]
    except Exception:  # table missing on a half-migrated DB → researched defaults
        pass
    return volumes


def parse_m3(value: Any) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise VolumeError("Enter a number of cubic metres, e.g. 1.2")
    if number != number or number < MIN_M3 or number > MAX_M3:
        raise VolumeError("m³ must be between {0} and {1:g}".format(MIN_M3, MAX_M3))
    return round(number, 3)


def _log(conn, key: str, old: float, new: float, action: str, note: str, user: str) -> None:
    conn.execute(
        "INSERT INTO furniture_item_volume_log "
        "(item_key, old_m3, new_m3, action, note, changed_at, changed_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (key, old, new, action, note, _now(), user),
    )


def set_volume(key: str, value: Any, user: str, note: str = "") -> float:
    """Set the standard m³ for ``key``. Setting it back to the researched default clears the override."""
    if key not in catalog.ITEMS:
        raise VolumeError("Unknown item")
    new = parse_m3(value)
    note = " ".join(str(note or "").split())[:MAX_NOTE]
    user = str(user or "")[:60]
    old = effective_volumes()[key]
    default = catalog.ITEMS[key][1]
    with db.get_connection() as conn:
        conn.execute("DELETE FROM furniture_item_volumes WHERE item_key = ?", (key,))
        if abs(new - default) > 1e-9:
            conn.execute(
                "INSERT INTO furniture_item_volumes (item_key, m3, note, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, new, note, _now(), user),
            )
        if abs(new - old) > 1e-9:
            _log(conn, key, old, new, "set" if abs(new - default) > 1e-9 else "reset", note, user)
        conn.commit()
    return new


def reset_volume(key: str, user: str) -> float:
    if key not in catalog.ITEMS:
        raise VolumeError("Unknown item")
    return set_volume(key, catalog.ITEMS[key][1], user, note="Reset to researched value")


def recent_changes(limit: int = 30) -> List[Dict[str, Any]]:
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT item_key, old_m3, new_m3, action, note, changed_at, changed_by "
            "FROM furniture_item_volume_log ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["label"] = catalog.ITEMS.get(item["item_key"], (item["item_key"],))[0]
        item["changed_display"] = _perth(item.get("changed_at"))
        result.append(item)
    return result


def _perth(iso_text: Optional[str]) -> str:
    import furniture_lists

    return furniture_lists.format_perth(iso_text)


def admin_rows() -> List[Dict[str, Any]]:
    """View model for the standard volumes admin page, grouped like the editor."""
    current = overrides()
    groups: List[Dict[str, Any]] = []
    for group_key, label in catalog.GROUP_LABELS.items():
        items = []
        for key, group in catalog.ITEM_GROUP.items():
            if group != group_key:
                continue
            item_label, default, _hint = catalog.ITEMS[key]
            ref = refs.REFS[key]
            override = current.get(key)
            items.append(
                {
                    "key": key,
                    "label": item_label,
                    "default_m3": default,
                    "current_m3": override["m3"] if override else default,
                    "override": override,
                    "updated_display": _perth(override.get("updated_at")) if override else "",
                    "legacy_m3": catalog.LEGACY_M3.get(key),
                    "basis": ref["basis"],
                    "basis_label": refs.BASIS_LABELS[ref["basis"]],
                    "reason": ref["reason"],
                    "refs": [
                        dict(refs.source_payload(src), label=src_label, m3=value)
                        for src, src_label, value in ref["refs"]
                    ],
                    "excluded": [
                        dict(refs.source_payload(src), label=src_label, m3=value, why=why)
                        for src, src_label, value, why in ref["excluded"]
                    ],
                    "outer": [
                        dict(refs.source_payload(src), label=src_label, dims=dims, m3=value)
                        for src, src_label, dims, value in ref["outer"]
                    ],
                    "derived_from": [
                        {"key": k, "label": catalog.ITEMS[k][0], "m3": catalog.ITEMS[k][1]}
                        for k in (ref["derived_from"] or ())
                    ],
                }
            )
        groups.append({"key": group_key, "label": label, "items": items})
    return groups
