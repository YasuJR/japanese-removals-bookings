"""Customer furniture list catalogue: rooms, items and standard volumes (m³).

Volumes are rough loaded-truck volumes used only for an *approximate* m³
figure. They never set a price or job duration. Where each number comes from
(sources, median, provisional) lives in furniture_volume_refs.py; admin
overrides live in furniture_volumes.py.

No double counting: every bed size has three mutually exclusive choices —
"Bed with mattress", "Bed frame only" and "Mattress only". A bed with its
mattress is ONE item; the mattress is never added again.

Each room stores the m³ per item at the time the item was added
(``room["unit_m3"]``), so changing a standard volume later never rewrites
lists that already contain that item.
"""

from typing import Any, Dict, List, Optional, Tuple

import furniture_volume_refs as refs

# Volumes in use before the 2026-10-27 review (no sources). Items saved before
# per-item volumes were stored keep these, so existing lists never change.
LEGACY_M3: Dict[str, float] = {
    "sofa_1": 0.8, "sofa_2": 1.2, "sofa_3": 1.7, "sofa_corner": 2.8, "sofa_bed": 1.8,
    "recliner": 1.0, "ottoman": 0.3, "coffee_table": 0.4, "side_table": 0.15, "tv_unit": 0.6,
    "tv_small": 0.2, "tv_large": 0.35, "bookshelf_small": 0.5, "bookshelf_large": 1.0,
    "display_cabinet": 1.0, "rug": 0.15, "floor_lamp": 0.15, "piano_upright": 1.5,
    "mirror_large": 0.2, "bed_single": 1.0, "bed_king_single": 1.2, "bed_double": 1.5,
    "bed_queen": 1.8, "bed_king": 2.1, "bed_frame_single": 0.6, "bed_frame_king_single": 0.7,
    "bed_frame_double": 0.9, "bed_frame_queen": 1.0, "bed_frame_king": 1.2,
    "mattress_single": 0.4, "mattress_king_single": 0.45, "mattress_double": 0.55,
    "mattress_queen": 0.65, "mattress_king": 0.75, "bunk_bed": 2.0, "cot": 0.5,
    "bedside_table": 0.2, "chest_drawers": 0.6, "tallboy": 0.7, "dresser": 0.9,
    "wardrobe_1": 0.9, "wardrobe_2": 1.5, "wardrobe_3": 2.2, "desk": 0.8,
    "dining_table_4": 0.8, "dining_table_6": 1.1, "dining_table_8": 1.5, "dining_chair": 0.2,
    "bar_stool": 0.15, "buffet": 0.9, "bench_seat": 0.4, "fridge_single": 0.8,
    "fridge_double": 1.2, "fridge_large": 1.6, "bar_fridge": 0.3, "freezer": 0.8,
    "microwave": 0.1, "dishwasher": 0.5, "kitchen_cabinet": 0.8, "washing_machine": 0.6,
    "dryer": 0.5, "ironing_board": 0.1, "laundry_basket": 0.1, "office_chair": 0.4,
    "filing_cabinet": 0.4, "computer": 0.1, "printer": 0.1, "shelf_small": 0.4,
    "shelf_large": 0.8, "plastic_tub": 0.08, "outdoor_table": 0.9, "outdoor_chair": 0.25,
    "outdoor_lounge": 2.0, "sun_lounger": 0.5, "bbq": 0.8, "pot_plant_small": 0.1,
    "pot_plant_large": 0.3, "bicycle": 0.5, "lawn_mower": 0.5, "tool_box": 0.1,
    "treadmill": 1.0, "exercise_bike": 0.5, "box_small": 0.05, "box_medium": 0.1,
    "box_large": 0.15, "box_wardrobe": 0.4, "suitcase": 0.15, "bag": 0.08,
}

# (key, label, hint)
_BED_SIZES = [
    ("single", "Single"),
    ("king_single", "King single"),
    ("double", "Double"),
    ("queen", "Queen"),
    ("king", "King"),
]


def _bed_items() -> List[Tuple[str, str, str]]:
    items: List[Tuple[str, str, str]] = []
    for key, label in _BED_SIZES:
        items.append(("bed_{0}".format(key), "{0} bed + mattress".format(label), "Frame and mattress together"))
    for key, label in _BED_SIZES:
        items.append(("bed_frame_{0}".format(key), "{0} bed frame only".format(label), "No mattress"))
    for key, label in _BED_SIZES:
        items.append(("mattress_{0}".format(key), "{0} mattress only".format(label), "Mattress without a frame"))
    return items


_GROUPS: Dict[str, List[Tuple[str, str, str]]] = {
    "seating": [
        ("sofa_1", "Armchair / 1-seater sofa", ""),
        ("sofa_2", "2-seater sofa", ""),
        ("sofa_3", "3-seater sofa", ""),
        ("sofa_corner", "Corner / L-shaped sofa", ""),
        ("sofa_bed", "Sofa bed (2-seater)", ""),
        ("recliner", "Recliner chair", ""),
        ("ottoman", "Ottoman / footstool", ""),
    ],
    "living": [
        ("coffee_table", "Coffee table", ""),
        ("side_table", "Side table", ""),
        ("tv_unit", "TV unit / entertainment unit", ""),
        ("tv_small", "TV up to 55\"", ""),
        ("tv_large", "TV over 55\"", ""),
        ("bookshelf_small", "Bookshelf (small)", ""),
        ("bookshelf_large", "Bookshelf (large)", ""),
        ("display_cabinet", "Display cabinet", ""),
        ("rug", "Rug (rolled)", ""),
        ("floor_lamp", "Floor lamp", ""),
        ("piano_upright", "Upright piano", "Heavy item"),
        ("mirror_large", "Large mirror / artwork", ""),
    ],
    "beds": _bed_items()
    + [
        ("bunk_bed", "Bunk bed (incl. mattresses)", ""),
        ("cot", "Baby cot (assembled)", ""),
        ("cot_dismantled", "Baby cot (dismantled)", ""),
    ],
    "bedroom": [
        ("bedside_table", "Bedside table", ""),
        ("chest_drawers", "Chest of drawers", ""),
        ("tallboy", "Tallboy", ""),
        ("dresser", "Dressing table / dresser", ""),
        ("wardrobe_1", "Wardrobe (1 door)", "Freestanding only"),
        ("wardrobe_2", "Wardrobe (2 doors)", "Freestanding only"),
        ("wardrobe_3", "Wardrobe (3+ doors)", "Freestanding only"),
        ("tv_small", "TV up to 55\"", ""),
        ("desk", "Desk", ""),
        ("mirror_large", "Large mirror / artwork", ""),
    ],
    "dining": [
        ("dining_table_4", "Dining table (4 seats)", ""),
        ("dining_table_6", "Dining table (6 seats)", ""),
        ("dining_table_8", "Dining table (8+ seats)", ""),
        ("dining_chair", "Dining chair", ""),
        ("bar_stool", "Bar stool", ""),
        ("buffet", "Buffet / sideboard", ""),
        ("bench_seat", "Bench seat", ""),
    ],
    "kitchen": [
        ("fridge_single", "Fridge (small)", ""),
        ("fridge_double", "Fridge (standard)", "Top or bottom freezer"),
        ("fridge_large", "Fridge (French door / side-by-side)", ""),
        ("bar_fridge", "Bar fridge", ""),
        ("freezer", "Chest freezer", ""),
        ("freezer_upright", "Upright freezer", ""),
        ("microwave", "Microwave", ""),
        ("dishwasher", "Dishwasher (freestanding)", ""),
        ("kitchen_cabinet", "Pantry / storage cabinet", ""),
    ],
    "laundry": [
        ("washing_machine", "Washing machine", ""),
        ("dryer", "Dryer", ""),
        ("ironing_board", "Ironing board", ""),
        ("laundry_basket", "Laundry basket", ""),
    ],
    "study": [
        ("desk", "Desk", ""),
        ("office_chair", "Office chair", ""),
        ("filing_cabinet", "Filing cabinet", ""),
        ("computer", "Computer / monitor", ""),
        ("printer", "Printer", ""),
    ],
    "storage": [
        ("shelf_small", "Shelving unit (small)", ""),
        ("shelf_large", "Shelving unit (large)", ""),
        ("plastic_tub", "Plastic storage tub", ""),
    ],
    "outdoor": [
        ("outdoor_table", "Outdoor table", ""),
        ("outdoor_chair", "Outdoor chair", ""),
        ("outdoor_lounge", "Outdoor lounge set", ""),
        ("sun_lounger", "Sun lounger", ""),
        ("bbq", "BBQ", ""),
        ("pot_plant_small", "Pot plant (small)", ""),
        ("pot_plant_large", "Pot plant (large)", ""),
        ("bicycle", "Bicycle", ""),
        ("lawn_mower", "Lawn mower", ""),
        ("tool_box", "Tool box", ""),
        ("treadmill", "Treadmill", "Heavy item"),
        ("exercise_bike", "Exercise bike", ""),
    ],
    "boxes": [
        ("box_small", "Small box (book box)", "About 40 × 30 × 33 cm"),
        ("box_medium", "Medium box (standard carton)", "About 41 × 30 × 43 cm"),
        ("box_large", "Large box (tea chest)", "About 43 × 41 × 60 cm"),
        ("box_wardrobe", "Wardrobe box", ""),
        ("suitcase", "Suitcase", ""),
        ("bag", "Bag / bundle", ""),
    ],
}

GROUP_LABELS: Dict[str, str] = {
    "seating": "Sofas & chairs",
    "living": "Living",
    "beds": "Beds",
    "bedroom": "Bedroom",
    "dining": "Dining",
    "kitchen": "Kitchen",
    "laundry": "Laundry",
    "study": "Study",
    "storage": "Storage",
    "outdoor": "Outdoor / garage",
    "boxes": "Boxes & bags",
}

# key -> (label, researched default m³, hint)
ITEMS: Dict[str, Tuple[str, float, str]] = {}
ITEM_GROUP: Dict[str, str] = {}
for _group, _group_items in _GROUPS.items():
    for _key, _label, _hint in _group_items:
        if _key not in ITEMS:
            ITEMS[_key] = (_label, refs.researched_m3(_key), _hint)
            ITEM_GROUP[_key] = _group

MAX_UNIT_M3 = 20.0


def default_volumes() -> Dict[str, float]:
    return {key: m3 for key, (_label, m3, _hint) in ITEMS.items()}


def _volume_for(key: str, volumes: Optional[Dict[str, float]]) -> float:
    if volumes and key in volumes:
        return float(volumes[key])
    return ITEMS[key][1]


# Room types: (key, label, groups shown)
ROOM_TYPES: List[Tuple[str, str, List[str]]] = [
    ("living", "Living room", ["seating", "living", "storage"]),
    ("bedroom", "Bedroom", ["beds", "bedroom"]),
    ("dining", "Dining room", ["dining", "storage"]),
    ("kitchen", "Kitchen", ["kitchen", "dining"]),
    ("laundry", "Laundry", ["laundry", "storage"]),
    ("study", "Study / office", ["study", "living", "storage"]),
    ("outdoor", "Outdoor / garage", ["outdoor", "storage"]),
    ("boxes", "Boxes & bags", ["boxes"]),
]
ROOM_TYPE_MAP = {key: (label, groups) for key, label, groups in ROOM_TYPES}

DEFAULT_ROOMS: List[Tuple[str, str]] = [
    ("living", "Living room"),
    ("bedroom", "Bedroom 1"),
    ("kitchen", "Kitchen"),
    ("dining", "Dining room"),
    ("laundry", "Laundry"),
    ("outdoor", "Outdoor / garage"),
    ("boxes", "Boxes & bags"),
]

MAX_QTY = 200
MAX_ROOMS = 30
MAX_CUSTOM_ITEMS = 100
MAX_DIMENSION_CM = 1000
MAX_TEXT = 200
MAX_NOTES = 4000


def catalog_payload(volumes: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Catalogue for the browser editor. ``volumes`` = current standard m³ (admin overrides)."""
    rooms = []
    for key, label, groups in ROOM_TYPES:
        item_keys: List[str] = []
        for group in groups:
            for item_key, _l, _h in _GROUPS[group]:
                if item_key not in item_keys:
                    item_keys.append(item_key)
        rooms.append({"type": key, "label": label, "items": item_keys})
    return {
        "items": {
            key: {"label": label, "m3": _volume_for(key, volumes), "hint": hint}
            for key, (label, _m3, hint) in ITEMS.items()
        },
        "room_types": rooms,
        "default_rooms": [{"type": t, "label": l} for t, l in DEFAULT_ROOMS],
    }


def _clean_text(value: Any, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _int(value: Any, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def _dim(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number > MAX_DIMENSION_CM:
        return None
    return round(number, 1)


def default_content() -> Dict[str, Any]:
    return {
        "rooms": [
            {"id": "r{0}".format(i + 1), "type": t, "label": l, "items": {}}
            for i, (t, l) in enumerate(DEFAULT_ROOMS)
        ],
        "custom_items": [],
        "notes": "",
    }


def _unit(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > MAX_UNIT_M3:
        return None
    return round(number, 3)


def sanitize_content(raw: Any, keep_units: bool = False) -> Dict[str, Any]:
    """Validate JSON into the stored shape. Unknown keys are dropped.

    ``keep_units`` keeps the stored per-item m³ (``unit_m3``); only pass it for
    content read back from the database. Browser input never sets volumes —
    ``freeze_units`` assigns them on save.
    """
    if not isinstance(raw, dict):
        raw = {}
    rooms: List[Dict[str, Any]] = []
    seen_ids = set()
    for room in (raw.get("rooms") or [])[:MAX_ROOMS]:
        if not isinstance(room, dict):
            continue
        room_type = str(room.get("type") or "")
        if room_type not in ROOM_TYPE_MAP:
            continue
        room_id = _clean_text(room.get("id"), 20) or "r{0}".format(len(rooms) + 1)
        if room_id in seen_ids:
            room_id = "r{0}x{1}".format(len(rooms) + 1, len(seen_ids))
        seen_ids.add(room_id)
        items: Dict[str, int] = {}
        raw_items = room.get("items") if isinstance(room.get("items"), dict) else {}
        for key, qty in raw_items.items():
            if key in ITEMS:
                count = _int(qty, 0, MAX_QTY)
                if count > 0:
                    items[key] = count
        cleaned = {
            "id": room_id,
            "type": room_type,
            "label": _clean_text(room.get("label"), 60) or ROOM_TYPE_MAP[room_type][0],
            "items": items,
        }
        if keep_units:
            raw_units = room.get("unit_m3") if isinstance(room.get("unit_m3"), dict) else {}
            units = {}
            for key in items:
                unit = _unit(raw_units.get(key))
                # Saved before per-item volumes existed → the volume in use back then.
                units[key] = unit if unit is not None else LEGACY_M3.get(key, ITEMS[key][1])
            cleaned["unit_m3"] = units
        rooms.append(cleaned)
    custom: List[Dict[str, Any]] = []
    for item in (raw.get("custom_items") or [])[:MAX_CUSTOM_ITEMS]:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"))
        if not name:
            continue
        custom.append(
            {
                "name": name,
                "qty": _int(item.get("qty"), 1, MAX_QTY),
                "room": _clean_text(item.get("room"), 60),
                "length_cm": _dim(item.get("length_cm")),
                "width_cm": _dim(item.get("width_cm")),
                "height_cm": _dim(item.get("height_cm")),
                "note": _clean_text(item.get("note")),
            }
        )
    notes = str(raw.get("notes") or "").replace("\r\n", "\n").strip()[:MAX_NOTES]
    return {"rooms": rooms, "custom_items": custom, "notes": notes}


def freeze_units(
    content: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
    volumes: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Give every item in ``content`` its m³ per unit.

    An item already in the same room of ``previous`` (the stored list) keeps its
    stored m³, even when its quantity changes. Items newly added take the current
    standard volume. So a changed standard only applies to newly added items.
    """
    prev_rooms = {}
    for room in (previous or {}).get("rooms") or []:
        prev_rooms[room.get("id")] = room
    for room in content.get("rooms") or []:
        before = prev_rooms.get(room.get("id")) or {}
        before_items = before.get("items") or {}
        before_units = before.get("unit_m3") or {}
        units = {}
        for key in room.get("items") or {}:
            unit = _unit(before_units.get(key)) if before_items.get(key) else None
            units[key] = unit if unit is not None else _volume_for(key, volumes)
        room["unit_m3"] = units
    return content


def room_units(content: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """{room id: {item key: m³ per unit}} for the editor after a save."""
    return {room.get("id"): dict(room.get("unit_m3") or {}) for room in content.get("rooms") or []}


def custom_item_m3(item: Dict[str, Any]) -> Optional[float]:
    dims = [item.get("length_cm"), item.get("width_cm"), item.get("height_cm")]
    if any(d is None for d in dims):
        return None
    return dims[0] * dims[1] * dims[2] / 1_000_000.0


def summarize(content: Dict[str, Any], volumes: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Approximate m³ and counts. Custom items without all 3 sizes are not counted in m³.

    Uses each item's stored ``unit_m3``; items without one use ``volumes`` (or the default).
    """
    total = 0.0
    item_count = 0
    rooms_out = []
    for room in content.get("rooms") or []:
        lines = []
        units = room.get("unit_m3") or {}
        for key, qty in (room.get("items") or {}).items():
            label = ITEMS[key][0]
            m3 = units.get(key)
            if m3 is None:
                m3 = _volume_for(key, volumes)
            total += m3 * qty
            item_count += qty
            lines.append(
                {"key": key, "label": label, "qty": qty, "unit_m3": m3, "m3": round(m3 * qty, 2)}
            )
        lines.sort(key=lambda line: line["label"])
        rooms_out.append(
            {"id": room.get("id"), "label": room.get("label"), "type": room.get("type"), "lines": lines}
        )
    custom_lines = []
    unsized = 0
    for item in content.get("custom_items") or []:
        each = custom_item_m3(item)
        qty = int(item.get("qty") or 1)
        item_count += qty
        if each is None:
            unsized += qty
        else:
            total += each * qty
        dims = ""
        if each is not None:
            dims = "{0:g} × {1:g} × {2:g} cm".format(
                item["length_cm"], item["width_cm"], item["height_cm"]
            )
        custom_lines.append(
            {
                "name": item.get("name"),
                "qty": qty,
                "room": item.get("room") or "",
                "dims": dims,
                "note": item.get("note") or "",
                "m3": round(each * qty, 2) if each is not None else None,
            }
        )
    return {
        "estimated_m3": round(total, 1),
        "item_count": item_count,
        "unsized_custom_count": unsized,
        "rooms": [r for r in rooms_out if r["lines"]],
        "custom_items": custom_lines,
        "notes": content.get("notes") or "",
    }


def _custom_key(item: Dict[str, Any]) -> str:
    return " ".join(str(item.get("name") or "").lower().split())


def _custom_sig(item: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        int(item.get("qty") or 1),
        item.get("length_cm"),
        item.get("width_cm"),
        item.get("height_cm"),
        _clean_text(item.get("note")),
        _clean_text(item.get("room"), 60),
    )


def compare_to_baseline(content: Dict[str, Any], baseline: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What changed since the first submitted version (``baseline``).

    Rooms are matched by id, items by key; "Other items" by name. Used to show
    second-and-later updates in yellow (office, staff, PDF).
    """
    result: Dict[str, Any] = {
        "active": baseline is not None,
        "items": {},          # room id -> {item key: qty before (0 = newly added)}
        "removed": [],        # {"room", "label", "qty"}
        "custom": {},         # index in content["custom_items"] -> "new" | "changed"
        "removed_custom": [],  # {"name", "qty"}
        "notes_changed": False,
        "count": 0,
    }
    if baseline is None:
        return result
    before_rooms = {room.get("id"): room for room in baseline.get("rooms") or []}
    seen_ids = set()
    for room in content.get("rooms") or []:
        room_id = room.get("id")
        seen_ids.add(room_id)
        before_items = (before_rooms.get(room_id) or {}).get("items") or {}
        changed = {}
        for key, qty in (room.get("items") or {}).items():
            if before_items.get(key, 0) != qty:
                changed[key] = before_items.get(key, 0)
        for key, qty in before_items.items():
            if key not in (room.get("items") or {}) and key in ITEMS:
                result["removed"].append({"room": room.get("label"), "label": ITEMS[key][0], "qty": qty})
        if changed:
            result["items"][room_id] = changed
    for room_id, room in before_rooms.items():
        if room_id in seen_ids:
            continue
        for key, qty in (room.get("items") or {}).items():
            if key in ITEMS:
                result["removed"].append({"room": room.get("label"), "label": ITEMS[key][0], "qty": qty})
    before_custom: Dict[str, List[Dict[str, Any]]] = {}
    for item in baseline.get("custom_items") or []:
        before_custom.setdefault(_custom_key(item), []).append(item)
    for index, item in enumerate(content.get("custom_items") or []):
        matches = before_custom.get(_custom_key(item)) or []
        if not matches:
            result["custom"][index] = "new"
            continue
        previous = matches.pop(0)
        if _custom_sig(previous) != _custom_sig(item):
            result["custom"][index] = "changed"
    for leftovers in before_custom.values():
        for item in leftovers:
            result["removed_custom"].append({"name": item.get("name"), "qty": int(item.get("qty") or 1)})
    result["notes_changed"] = (baseline.get("notes") or "").strip() != (content.get("notes") or "").strip()
    result["count"] = (
        sum(len(v) for v in result["items"].values())
        + len(result["removed"])
        + len(result["custom"])
        + len(result["removed_custom"])
        + (1 if result["notes_changed"] else 0)
    )
    return result


def annotate_changes(summary: Dict[str, Any], changes: Dict[str, Any]) -> Dict[str, Any]:
    """Mark summary lines changed since the first submission (for yellow highlighting)."""
    summary["changes"] = changes
    for room in summary.get("rooms") or []:
        room_changes = (changes.get("items") or {}).get(room.get("id")) or {}
        for line in room["lines"]:
            if line["key"] in room_changes:
                line["changed"] = True
                line["before_qty"] = room_changes[line["key"]]
    for index, item in enumerate(summary.get("custom_items") or []):
        if index in (changes.get("custom") or {}):
            item["changed"] = changes["custom"][index]
    return summary


def has_content(content: Dict[str, Any]) -> bool:
    summary = summarize(content)
    return bool(summary["item_count"] or summary["notes"])
