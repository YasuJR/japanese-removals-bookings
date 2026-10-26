"""Customer furniture list catalogue: rooms, items and standard volumes (m³).

Volumes are rough loaded-truck volumes used only for an *approximate* m³
figure. They never set a price or job duration.

No double counting: every bed size has three mutually exclusive choices —
"Bed with mattress", "Bed frame only" and "Mattress only". A bed with its
mattress is ONE item; the mattress is never added again.
"""

from typing import Any, Dict, List, Optional, Tuple

# (key, label, m3, hint)
_BED_SIZES = [
    ("single", "Single", 1.0, 0.6, 0.4),
    ("king_single", "King single", 1.2, 0.7, 0.45),
    ("double", "Double", 1.5, 0.9, 0.55),
    ("queen", "Queen", 1.8, 1.0, 0.65),
    ("king", "King", 2.1, 1.2, 0.75),
]


def _bed_items() -> List[Tuple[str, str, float, str]]:
    items: List[Tuple[str, str, float, str]] = []
    for key, label, full, frame, mattress in _BED_SIZES:
        items.append(("bed_{0}".format(key), "{0} bed + mattress".format(label), full, "Frame and mattress together"))
    for key, label, full, frame, mattress in _BED_SIZES:
        items.append(("bed_frame_{0}".format(key), "{0} bed frame only".format(label), frame, "No mattress"))
    for key, label, full, frame, mattress in _BED_SIZES:
        items.append(("mattress_{0}".format(key), "{0} mattress only".format(label), mattress, "Mattress without a frame"))
    return items


ITEMS: Dict[str, Tuple[str, float, str]] = {}
_GROUPS: Dict[str, List[Tuple[str, str, float, str]]] = {
    "seating": [
        ("sofa_1", "Armchair / 1-seater sofa", 0.8, ""),
        ("sofa_2", "2-seater sofa", 1.2, ""),
        ("sofa_3", "3-seater sofa", 1.7, ""),
        ("sofa_corner", "Corner / L-shaped sofa", 2.8, ""),
        ("sofa_bed", "Sofa bed", 1.8, ""),
        ("recliner", "Recliner chair", 1.0, ""),
        ("ottoman", "Ottoman / footstool", 0.3, ""),
    ],
    "living": [
        ("coffee_table", "Coffee table", 0.4, ""),
        ("side_table", "Side table", 0.15, ""),
        ("tv_unit", "TV unit / entertainment unit", 0.6, ""),
        ("tv_small", "TV up to 55\"", 0.2, ""),
        ("tv_large", "TV 65\" or larger", 0.35, ""),
        ("bookshelf_small", "Bookshelf (small)", 0.5, ""),
        ("bookshelf_large", "Bookshelf (large)", 1.0, ""),
        ("display_cabinet", "Display cabinet", 1.0, ""),
        ("rug", "Rug (rolled)", 0.15, ""),
        ("floor_lamp", "Floor lamp", 0.15, ""),
        ("piano_upright", "Upright piano", 1.5, "Heavy item"),
        ("mirror_large", "Large mirror / artwork", 0.2, ""),
    ],
    "beds": _bed_items()
    + [
        ("bunk_bed", "Bunk bed (incl. mattresses)", 2.0, ""),
        ("cot", "Baby cot", 0.5, ""),
    ],
    "bedroom": [
        ("bedside_table", "Bedside table", 0.2, ""),
        ("chest_drawers", "Chest of drawers", 0.6, ""),
        ("tallboy", "Tallboy", 0.7, ""),
        ("dresser", "Dresser with mirror", 0.9, ""),
        ("wardrobe_1", "Wardrobe (1 door)", 0.9, "Freestanding only"),
        ("wardrobe_2", "Wardrobe (2 doors)", 1.5, "Freestanding only"),
        ("wardrobe_3", "Wardrobe (3+ doors)", 2.2, "Freestanding only"),
        ("tv_small", "TV up to 55\"", 0.2, ""),
        ("desk", "Desk", 0.8, ""),
        ("mirror_large", "Large mirror / artwork", 0.2, ""),
    ],
    "dining": [
        ("dining_table_4", "Dining table (4 seats)", 0.8, ""),
        ("dining_table_6", "Dining table (6 seats)", 1.1, ""),
        ("dining_table_8", "Dining table (8+ seats)", 1.5, ""),
        ("dining_chair", "Dining chair", 0.2, ""),
        ("bar_stool", "Bar stool", 0.15, ""),
        ("buffet", "Buffet / sideboard", 0.9, ""),
        ("bench_seat", "Bench seat", 0.4, ""),
    ],
    "kitchen": [
        ("fridge_single", "Fridge (single door)", 0.8, ""),
        ("fridge_double", "Fridge (2 doors / top freezer)", 1.2, ""),
        ("fridge_large", "Fridge (French door / side-by-side)", 1.6, ""),
        ("bar_fridge", "Bar fridge", 0.3, ""),
        ("freezer", "Chest / upright freezer", 0.8, ""),
        ("microwave", "Microwave", 0.1, ""),
        ("dishwasher", "Dishwasher (freestanding)", 0.5, ""),
        ("kitchen_cabinet", "Pantry / storage cabinet", 0.8, ""),
    ],
    "laundry": [
        ("washing_machine", "Washing machine", 0.6, ""),
        ("dryer", "Dryer", 0.5, ""),
        ("ironing_board", "Ironing board", 0.1, ""),
        ("laundry_basket", "Laundry basket", 0.1, ""),
    ],
    "study": [
        ("desk", "Desk", 0.8, ""),
        ("office_chair", "Office chair", 0.4, ""),
        ("filing_cabinet", "Filing cabinet", 0.4, ""),
        ("computer", "Computer / monitor", 0.1, ""),
        ("printer", "Printer", 0.1, ""),
    ],
    "storage": [
        ("shelf_small", "Shelving unit (small)", 0.4, ""),
        ("shelf_large", "Shelving unit (large)", 0.8, ""),
        ("plastic_tub", "Plastic storage tub", 0.08, ""),
    ],
    "outdoor": [
        ("outdoor_table", "Outdoor table", 0.9, ""),
        ("outdoor_chair", "Outdoor chair", 0.25, ""),
        ("outdoor_lounge", "Outdoor lounge set", 2.0, ""),
        ("sun_lounger", "Sun lounger", 0.5, ""),
        ("bbq", "BBQ", 0.8, ""),
        ("pot_plant_small", "Pot plant (small)", 0.1, ""),
        ("pot_plant_large", "Pot plant (large)", 0.3, ""),
        ("bicycle", "Bicycle", 0.5, ""),
        ("lawn_mower", "Lawn mower", 0.5, ""),
        ("tool_box", "Tool box", 0.1, ""),
        ("treadmill", "Treadmill", 1.0, "Heavy item"),
        ("exercise_bike", "Exercise bike", 0.5, ""),
    ],
    "boxes": [
        ("box_small", "Small box (books)", 0.05, "Approx. 40 × 30 × 40 cm"),
        ("box_medium", "Medium box (standard)", 0.1, "Approx. 45 × 40 × 55 cm"),
        ("box_large", "Large box", 0.15, "Approx. 60 × 45 × 55 cm"),
        ("box_wardrobe", "Wardrobe box", 0.4, ""),
        ("suitcase", "Suitcase", 0.15, ""),
        ("bag", "Bag / bundle", 0.08, ""),
    ],
}

for _group_items in _GROUPS.values():
    for _key, _label, _m3, _hint in _group_items:
        ITEMS[_key] = (_label, _m3, _hint)

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


def catalog_payload() -> Dict[str, Any]:
    """Catalogue for the browser editor."""
    rooms = []
    for key, label, groups in ROOM_TYPES:
        item_keys: List[str] = []
        for group in groups:
            for item_key, _l, _m, _h in _GROUPS[group]:
                if item_key not in item_keys:
                    item_keys.append(item_key)
        rooms.append({"type": key, "label": label, "items": item_keys})
    return {
        "items": {
            key: {"label": label, "m3": m3, "hint": hint}
            for key, (label, m3, hint) in ITEMS.items()
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


def sanitize_content(raw: Any) -> Dict[str, Any]:
    """Validate browser JSON into the stored shape. Unknown keys are dropped."""
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
        rooms.append(
            {
                "id": room_id,
                "type": room_type,
                "label": _clean_text(room.get("label"), 60) or ROOM_TYPE_MAP[room_type][0],
                "items": items,
            }
        )
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


def custom_item_m3(item: Dict[str, Any]) -> Optional[float]:
    dims = [item.get("length_cm"), item.get("width_cm"), item.get("height_cm")]
    if any(d is None for d in dims):
        return None
    return dims[0] * dims[1] * dims[2] / 1_000_000.0


def summarize(content: Dict[str, Any]) -> Dict[str, Any]:
    """Approximate m³ and counts. Custom items without all 3 sizes are not counted in m³."""
    total = 0.0
    item_count = 0
    rooms_out = []
    for room in content.get("rooms") or []:
        lines = []
        for key, qty in (room.get("items") or {}).items():
            label, m3, _hint = ITEMS[key]
            total += m3 * qty
            item_count += qty
            lines.append({"key": key, "label": label, "qty": qty, "m3": round(m3 * qty, 2)})
        lines.sort(key=lambda line: line["label"])
        rooms_out.append({"label": room.get("label"), "type": room.get("type"), "lines": lines})
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


def has_content(content: Dict[str, Any]) -> bool:
    summary = summarize(content)
    return bool(summary["item_count"] or summary["notes"])
