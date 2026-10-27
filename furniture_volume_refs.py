"""Where every standard furniture volume (m³) comes from.

Purpose: an approximate *truck load* volume for customer furniture lists. It
never sets a price or job duration.

Rules used to pick each researched default (checked 2026-10-27):

* Only Australian removalists' published per-item load volumes are used
  ("removalist" sources). Each source contributes at most one value per item.
* A value is used only when the source's item, size and condition (e.g. bed
  "with mattress", cot "assembled") match ours. Values with a different or
  unclear condition are listed under ``excluded`` with the reason, never mixed in.
* Two or more matching values → their median (``median``). One → that value
  (``single``). A bed frame alone has no consistent published value, so it is
  bed-with-mattress minus mattress-only (``derived``) so the three bed choices add
  up. No usable value → a provisional value (``provisional``), clearly marked.
* Manufacturer / measured product sizes are a different thing (outer volume of
  the item, no blankets or loading gaps). They are kept separately in ``outer``
  for comparison only and never used as the load volume.

Admin overrides (furniture_volumes.py) replace the researched default for new
items only; lists keep the m³ each item had when it was added.
"""

from statistics import median
from typing import Any, Dict, List, Optional, Tuple

CHECKED_ON = "2026-10-27"

MEDIAN = "median"
SINGLE = "single"
DERIVED = "derived"
PROVISIONAL = "provisional"

BASIS_LABELS = {
    MEDIAN: "Median of removalist values",
    SINGLE: "One removalist value",
    DERIVED: "Derived (bed − mattress)",
    PROVISIONAL: "Provisional (no source)",
}

REMOVALIST = "removalist"
PRODUCT = "product"

# id: (name, url, kind, note)
SOURCES: Dict[str, Tuple[str, str, str, str]] = {
    "youpack": (
        "You Pack Removals — Furniture Volume Calculator",
        "https://www.youpack.com.au/furniture-calculator/",
        REMOVALIST,
        "Calculator presets. Bed values do not say whether the mattress is included.",
    ),
    "goldline": (
        "Goldline Removals — Cubic Metre Calculator",
        "https://goldlineremovals.com.au/cubic-meter-calculator/",
        REMOVALIST,
        "'Cubic Meters for Common Items' table. Beds and mattresses are separate rows.",
    ),
    "easypeasy": (
        "Easy Peasy Removals — Cubic Metre Calculator",
        "https://easypeasyremovals.com.au/cubic-meter-calculator/",
        REMOVALIST,
        "Calculator presets used. The page's how-to text quotes different figures "
        "(queen bed 1.2, fridge 0.8); those are not used.",
    ),
    "yms": (
        "Your Moving Solution — Furniture Volume Calculator",
        "https://yourmovingsolution.com.au/furniture-volume-calculator/",
        REMOVALIST,
        "Calculator presets. Double and queen/king beds are labelled '(frame + mattress)'.",
    ),
    "quicknslick": (
        "Quick 'n' Slick Removals — Moving Cost Calculator",
        "https://quicknslickremovals.com.au/moving-cost-calculator/",
        REMOVALIST,
        "Calculator presets. Beds are 'Bed & Mattress'; mattresses also listed alone.",
    ),
    "movingwithsam": (
        "Moving With Sam — Moving Volume Calculator",
        "https://movingwithsam.com.au/moving-volume-calculator/",
        REMOVALIST,
        "'Indicative values' list only (calculator items show no per-item values).",
    ),
    "ttmoving": (
        "TT Moving — How to calculate cubic metres",
        "https://ttmoving.com.au/blog/how-to-calculate-cubic-metres-moving",
        REMOVALIST,
        "'Volumes are the averages our crews load against.'",
    ),
    "getngo": (
        "Get n Go Removals — How to calculate cubic metres",
        "https://www.getngoremovals.com.au/how-to-calculate-cubic-metres/",
        REMOVALIST,
        "Averages table used as load volume; its worked measuring examples are outer sizes.",
    ),
    "a1": (
        "A1 Removalists Sydney — Furniture volume guide",
        "https://www.a1removalistssydney.com.au/blog/how-to-calculate-cubic-metres-for-moving-furniture-volume-guide/",
        REMOVALIST,
        "Gives ranges only (e.g. 3-seater 1.5–2.0). Ranges are shown for reference, "
        "not used in medians.",
    ),
    "fragile": (
        "Fragile Removals — How to measure furniture in cubic metres",
        "https://www.fragileremovals.com.au/moving-tips/how-to-measure-your-furniture-in-cubic-metres/",
        PRODUCT,
        "Tape-measure examples (outer size), not load volumes.",
    ),
    "findamover": (
        "Find a Mover — Moving box sizes",
        "https://www.findamover.com.au/blog/moving-box-sizes-and-their-specific-uses",
        PRODUCT,
        "Carton dimensions in mm (outer size).",
    ),
    "ao_fp_washer": (
        "Appliances Online — Fisher & Paykel WH80F60W1 8kg front loader",
        "https://www.appliancesonline.com.au/product/8kg-front-load-fisher-paykel-washing-machine-wh80f60w1",
        PRODUCT,
        "Manufacturer dimensions H 840 × W 600 × D 590 mm.",
    ),
    "ao_midea_fridge": (
        "Appliances Online — Midea MTM400W 400L top mount fridge",
        "https://www.appliancesonline.com.au/product/midea-mtm400w-400l-top-mount-fridge",
        PRODUCT,
        "Manufacturer dimensions H 1712 × W 696 × D 685 mm.",
    ),
}

R = Tuple[str, str, float]            # (source id, label on the source, m³)
X = Tuple[str, str, Any, str]         # (source id, label, m³ or range text, why excluded)
O = Tuple[str, str, str, float]       # (source id, item, dimensions, outer m³)


def _ref(
    basis: str,
    refs: Optional[List[R]] = None,
    reason: str = "",
    excluded: Optional[List[X]] = None,
    outer: Optional[List[O]] = None,
    value: Optional[float] = None,
    derived_from: Optional[Tuple[str, str]] = None,
) -> Dict[str, Any]:
    return {
        "basis": basis,
        "refs": refs or [],
        "reason": reason,
        "excluded": excluded or [],
        "outer": outer or [],
        "value": value,
        "derived_from": derived_from,
    }


_UNSTATED = "Not stated whether the mattress is included"
_BED_COND = "Removalists do not say whether the bed is dismantled; values are used as published."

REFS: Dict[str, Dict[str, Any]] = {
    # ------------------------------------------------------------ seating
    "sofa_1": _ref(MEDIAN, [
        ("youpack", "Arm Chair [Lounge]", 0.48),
        ("easypeasy", "Armchair / recliner", 0.70),
        ("yms", "Armchair/Recliner", 0.80),
        ("quicknslick", "Lounge - Arm Chair", 1.00),
    ], "Median of the 4 removalists' armchair values.", excluded=[
        ("youpack", "Arm Chair [Family]", 0.40, "Same table lists it twice; the Lounge value is used"),
        ("a1", "Armchair", "0.6–0.9", "Range only"),
    ]),
    "sofa_2": _ref(MEDIAN, [
        ("youpack", "2 Seater Lounge", 1.00),
        ("easypeasy", "2-seater sofa", 1.30),
        ("yms", "2-Seater Sofa/Loveseat", 1.40),
        ("quicknslick", "Lounge - 2 Seater", 1.80),
        ("ttmoving", "2-seater lounge", 1.70),
    ], "Median of 5 removalists.", excluded=[("a1", "2-seater sofa", "1.1–1.4", "Range only")]),
    "sofa_3": _ref(MEDIAN, [
        ("youpack", "3 Seater Lounge", 1.60),
        ("easypeasy", "3-seater sofa", 1.80),
        ("yms", "3-Seater Sofa", 2.20),
        ("quicknslick", "Lounge - 3 Seater", 2.20),
        ("ttmoving", "3-seater lounge", 2.20),
        ("getngo", "3-Seater Sofa", 1.50),
    ], "Median of 6 removalists.", excluded=[
        ("a1", "3-seater sofa", "1.5–2.0", "Range only"),
    ], outer=[("fragile", "Three-seat couch (measuring example)", "218 × 88 × 88 cm", 1.69)]),
    "sofa_corner": _ref(SINGLE, [
        ("yms", "Corner/Modular Sofa", 3.50),
    ], "Only one removalist lists a corner sofa.", excluded=[
        ("quicknslick", "Lounge - Modular", 3.50, "'Modular' does not say corner/L-shape"),
        ("quicknslick", "Lounge - 2 Seater + Chaise", 2.80, "Seat count specified; ours is any size"),
        ("quicknslick", "Lounge - 3 Seater + Chaise", 3.50, "Seat count specified; ours is any size"),
    ], outer=[("getngo", "L-shaped sofa (measuring example)", "280 × 160 × 80 cm", 3.58)]),
    "sofa_bed": _ref(SINGLE, [
        ("youpack", "Sofa Bed (2 seater)", 1.00),
    ], "Only one removalist lists a sofa bed (2-seater); our label now says 2-seater."),
    "recliner": _ref(MEDIAN, [
        ("youpack", "Recliner", 0.40),
        ("easypeasy", "Armchair / recliner", 0.70),
        ("yms", "Armchair/Recliner", 0.80),
    ], "Median of 3 removalists."),
    "ottoman": _ref(MEDIAN, [
        ("yms", "Ottoman/Storage Bench", 0.30),
        ("quicknslick", "Lounge - Ottoman", 0.35),
    ], "Median of 2 removalists.", excluded=[("youpack", "Pouf", 0.25, "Different item name")]),
    # ------------------------------------------------------------ living
    "coffee_table": _ref(MEDIAN, [
        ("youpack", "Coffee Table", 0.20),
        ("goldline", "Coffee Table", 0.40),
        ("easypeasy", "Coffee table", 0.40),
        ("yms", "Coffee Table", 0.50),
        ("quicknslick", "Coffee Table", 0.30),
        ("movingwithsam", "Coffee Table", 0.40),
    ], "Median of 6 removalists.", excluded=[("a1", "Coffee table", "0.2–0.4", "Range only")]),
    "side_table": _ref(MEDIAN, [
        ("yms", "Side/End Table", 0.20),
        ("quicknslick", "Side Table / Lamp Table", 0.20),
    ], "Median of 2 removalists."),
    "tv_unit": _ref(MEDIAN, [
        ("easypeasy", "TV stand / entertainment unit", 0.60),
        ("yms", "TV Unit/Entertainment Cabinet", 0.90),
        ("quicknslick", "Entertainment Unit - Lowline", 0.40),
    ], "Median of 3 removalists.", excluded=[
        ("youpack", "Wall Unit", 1.00, "Different item (wall unit)"),
        ("a1", "TV unit", "0.4–0.9", "Range only"),
    ]),
    "tv_small": _ref(MEDIAN, [
        ("easypeasy", "TV (up to 55\")", 0.30),
        ("yms", "TV (up to 55\")", 0.40),
    ], "Median of 2 removalists with the same 55\" split.", excluded=[
        ("youpack", "TV (up to 80cm)", 0.40, "Different size split"),
        ("quicknslick", "TV", 0.50, "No size given"),
    ]),
    "tv_large": _ref(MEDIAN, [
        ("easypeasy", "TV (over 55\")", 0.50),
        ("yms", "TV (55\" or larger)", 0.60),
    ], "Median of 2 removalists. Label changed from 65\"+ to over 55\" to match the sources.", excluded=[
        ("youpack", "TV (Above 80cm)", 0.80, "Different size split"),
    ]),
    "bookshelf_small": _ref(SINGLE, [
        ("youpack", "Bookcase (Small)", 0.35),
    ], "Only one removalist splits bookcases by size.", excluded=[
        ("easypeasy", "Bookshelf", 0.80, "No size given"),
        ("quicknslick", "Bookshelf", 0.50, "No size given"),
        ("ttmoving", "Bookshelf", 1.00, "No size given"),
    ]),
    "bookshelf_large": _ref(SINGLE, [
        ("youpack", "Bookcase (Large)", 1.00),
    ], "Only one removalist splits bookcases by size.", excluded=[
        ("goldline", "Bookcase", 1.10, "No size given"),
        ("getngo", "Tall Bookcase", 0.80, "'Tall' rather than large"),
    ]),
    "display_cabinet": _ref(MEDIAN, [
        ("easypeasy", "Display cabinet", 1.00),
        ("yms", "Bookshelf/Display Cabinet", 1.20),
    ], "Median of 2 removalists.", excluded=[
        ("youpack", "China Cabinet", 0.85, "Different item name (china cabinet)"),
        ("yms", "China Cabinet", 1.30, "Different item name (china cabinet)"),
        ("quicknslick", "China Cabinet", 1.00, "Different item name (china cabinet)"),
    ]),
    "rug": _ref(MEDIAN, [
        ("easypeasy", "Rug (rolled)", 0.20),
        ("yms", "Rug (rolled)", 0.10),
    ], "Median of 2 removalists (rolled rug).", excluded=[
        ("youpack", "Rug", 0.10, "Not stated as rolled"),
    ]),
    "floor_lamp": _ref(MEDIAN, [
        ("easypeasy", "Standing lamp", 0.20),
        ("yms", "Standing/Floor Lamp", 0.15),
    ], "Median of 2 removalists.", excluded=[
        ("youpack", "Lamp", 0.10, "Lamp type not given"),
        ("quicknslick", "Lamp", 0.15, "Lamp type not given"),
    ]),
    "piano_upright": _ref(MEDIAN, [
        ("youpack", "Piano (upright)", 1.50),
        ("easypeasy", "Piano (upright)", 1.40),
        ("yms", "Upright Piano", 2.00),
    ], "Median of 3 removalists.", excluded=[
        ("quicknslick", "Piano - Upright / Grand", 1.00, "Also covers grand pianos"),
    ]),
    "mirror_large": _ref(SINGLE, [
        ("yms", "Large Mirror", 0.20),
    ], "Only one removalist lists a large mirror.", excluded=[
        ("quicknslick", "Pictures / Mirrors", 0.15, "Size not given"),
    ]),
    # ------------------------------------------------------------ beds (bed + mattress)
    "bed_single": _ref(SINGLE, [
        ("quicknslick", "Bed & Mattress - Single", 0.60),
    ], "Only one removalist states a single bed includes the mattress. " + _BED_COND, excluded=[
        ("youpack", "Bed Single", 0.60, _UNSTATED),
        ("easypeasy", "Bed (Single)", 1.00, _UNSTATED),
        ("yms", "Single/King Single Bed", 1.10, _UNSTATED + "; also mixes two sizes"),
    ]),
    "bed_king_single": _ref(
        PROVISIONAL,
        reason="No removalist gives a king single bed with mattress. Provisional value "
        "interpolated by mattress area between single (0.60) and double (1.25).",
        excluded=[
            ("youpack", "Bed King Single", 0.80, _UNSTATED),
            ("yms", "Single/King Single Bed", 1.10, _UNSTATED + "; also mixes two sizes"),
        ],
        value=0.93,
    ),
    "bed_double": _ref(MEDIAN, [
        ("yms", "Double Bed (frame + mattress)", 1.30),
        ("quicknslick", "Bed & Mattress - Double", 1.20),
    ], "Median of 2 removalists that include the mattress. " + _BED_COND, excluded=[
        ("youpack", "Bed Double", 1.20, _UNSTATED),
        ("easypeasy", "Bed (Double)", 1.40, _UNSTATED),
    ]),
    "bed_queen": _ref(MEDIAN, [
        ("yms", "Queen/King Bed (frame + mattress)", 1.60),
        ("quicknslick", "Bed & Mattress - Queen", 1.40),
        ("ttmoving", "Queen bed with mattress", 1.40),
        ("getngo", "Queen Bed & Mattress", 1.80),
    ], "Median of 4 removalists that include the mattress. " + _BED_COND, excluded=[
        ("youpack", "Bed Queen", 1.60, _UNSTATED),
        ("easypeasy", "Bed (Queen)", 1.70, _UNSTATED + "; page text also says 1.2"),
        ("goldline", "Queen Bed", 1.80, _UNSTATED + " (mattress listed separately)"),
        ("movingwithsam", "Double Bed – Queen", 1.80, _UNSTATED),
    ]),
    "bed_king": _ref(MEDIAN, [
        ("yms", "Queen/King Bed (frame + mattress)", 1.60),
        ("quicknslick", "Bed & Mattress - King", 2.00),
    ], "Median of 2 removalists that include the mattress. " + _BED_COND, excluded=[
        ("youpack", "Bed King", 2.00, _UNSTATED),
        ("easypeasy", "Bed (King)", 2.00, _UNSTATED),
        ("goldline", "King Bed", 2.20, _UNSTATED + " (mattress listed separately)"),
        ("movingwithsam", "Double Bed – King", 2.00, _UNSTATED),
    ]),
    # ------------------------------------------------------------ mattress only
    "mattress_single": _ref(MEDIAN, [
        ("goldline", "Single Mattress", 0.50),
        ("movingwithsam", "Single Mattress", 0.50),
    ], "Median of 2 removalists."),
    "mattress_king_single": _ref(
        PROVISIONAL,
        reason="No removalist gives a king single mattress. Provisional value interpolated "
        "by mattress area between single (0.50) and double (0.70).",
        value=0.60,
    ),
    "mattress_double": _ref(SINGLE, [
        ("quicknslick", "Mattress - Double", 0.70),
    ], "Only one removalist lists a double mattress."),
    "mattress_queen": _ref(MEDIAN, [
        ("goldline", "Queen Mattress", 0.70),
        ("quicknslick", "Mattress - Queen", 0.80),
        ("movingwithsam", "Queen Mattress", 0.80),
    ], "Median of 3 removalists.", excluded=[
        ("a1", "Queen bed mattress", "0.6–0.9", "Range only"),
    ], outer=[
        ("fragile", "Queen mattress (measuring example)", "203 × 153 × 25 cm", 0.78),
        ("getngo", "Queen mattress (measuring example)", "203 × 153 × 30 cm", 0.93),
    ]),
    "mattress_king": _ref(MEDIAN, [
        ("goldline", "King Mattress", 0.90),
        ("quicknslick", "Mattress - King", 1.00),
        ("movingwithsam", "King Mattress", 0.90),
    ], "Median of 3 removalists."),
    # ------------------------------------------------------------ frame only (derived)
    "bed_frame_single": _ref(
        DERIVED, derived_from=("bed_single", "mattress_single"),
        reason="Bed + mattress (0.60) − mattress (0.50). Small because the only bed + mattress "
        "value (Quick 'n' Slick) is low; check against your own jobs.",
        excluded=[
            ("goldline", "Single Bed Frame", 1.30, "Larger than every single bed + mattress value; "
             "assembled/dismantled not stated"),
            ("movingwithsam", "Single Bed Frame", 1.30, "Same as Goldline; same issue"),
            ("a1", "Bed frame", "0.4–1.0", "Range only, size not given"),
        ],
    ),
    "bed_frame_king_single": _ref(
        DERIVED, derived_from=("bed_king_single", "mattress_king_single"),
        reason="Bed + mattress − mattress (both provisional).",
    ),
    "bed_frame_double": _ref(
        DERIVED, derived_from=("bed_double", "mattress_double"),
        reason="Bed + mattress (1.25) − mattress (0.70).",
    ),
    "bed_frame_queen": _ref(
        DERIVED, derived_from=("bed_queen", "mattress_queen"),
        reason="Bed + mattress (1.50) − mattress (0.80).",
        excluded=[("goldline", "Queen Bed", 1.80, "Not stated as frame only")],
    ),
    "bed_frame_king": _ref(
        DERIVED, derived_from=("bed_king", "mattress_king"),
        reason="Bed + mattress (1.80) − mattress (0.90).",
        excluded=[("goldline", "King Bed", 2.20, "Not stated as frame only")],
    ),
    "bunk_bed": _ref(SINGLE, [
        ("quicknslick", "Bed & Mattress - Bunks", 1.25),
    ], "Only one removalist states bunks include mattresses.", excluded=[
        ("youpack", "Bunk Bed", 1.60, _UNSTATED),
        ("yms", "Bunk Bed", 1.80, _UNSTATED),
    ]),
    "cot": _ref(MEDIAN, [
        ("youpack", "Cot Assembled", 1.00),
        ("quicknslick", "Baby Cot - Assembled", 0.50),
    ], "Median of 2 removalists (assembled). Label now says assembled.", excluded=[
        ("yms", "Cot/Crib", 0.90, "Assembled or dismantled not stated"),
    ]),
    "cot_dismantled": _ref(MEDIAN, [
        ("youpack", "Cot Dismantled", 0.50),
        ("quicknslick", "Baby Cot - Dismantled", 0.20),
    ], "Median of 2 removalists (dismantled)."),
    # ------------------------------------------------------------ bedroom
    "bedside_table": _ref(MEDIAN, [
        ("youpack", "Bedside Table", 0.15),
        ("easypeasy", "Bedside table", 0.20),
        ("yms", "Bedside Table", 0.20),
        ("quicknslick", "Bedside Table / Drawer", 0.15),
    ], "Median of 4 removalists.", excluded=[("a1", "Bedside table", "0.15–0.3", "Range only")]),
    "chest_drawers": _ref(MEDIAN, [
        ("youpack", "Chest of Drawers", 0.85),
        ("easypeasy", "Chest of drawers", 0.70),
        ("yms", "Chest of Drawers", 0.90),
        ("quicknslick", "Chest of Drawers", 0.50),
        ("ttmoving", "Chest of drawers", 1.00),
    ], "Median of 5 removalists."),
    "tallboy": _ref(MEDIAN, [
        ("youpack", "Tallboy", 0.85),
        ("yms", "Tallboy", 1.00),
        ("quicknslick", "Tallboy", 0.50),
    ], "Median of 3 removalists.", excluded=[("a1", "Tallboy", "0.6–1.0", "Range only")]),
    "dresser": _ref(MEDIAN, [
        ("youpack", "Dressing Table", 1.00),
        ("easypeasy", "Dressing table / vanity", 0.70),
        ("yms", "Dressing Table/Mirror", 0.40),
        ("quicknslick", "Dressing Table", 1.00),
    ], "Median of 4 removalists (dressing table)."),
    "wardrobe_1": _ref(MEDIAN, [
        ("youpack", "Wardrobe single door (small)", 1.60),
        ("quicknslick", "Wardrobe - Single Door", 1.00),
    ], "Median of 2 removalists."),
    "wardrobe_2": _ref(MEDIAN, [
        ("youpack", "Wardrobe double door (Medium)", 1.60),
        ("quicknslick", "Wardrobe - Double Door", 1.50),
        ("ttmoving", "Double wardrobe", 1.50),
    ], "Median of 3 removalists.", excluded=[
        ("yms", "Wardrobe (2-3 door)", 1.40, "Covers 2 and 3 doors"),
        ("easypeasy", "Wardrobe (standard)", 1.40, "Door count not given"),
    ], outer=[("getngo", "Two-door wardrobe (measuring example)", "180 × 100 × 60 cm", 1.08)]),
    "wardrobe_3": _ref(MEDIAN, [
        ("youpack", "Wardrobe 3 door (medium)", 2.10),
        ("quicknslick", "Wardrobe - Three Door", 2.00),
    ], "Median of 2 removalists (3 doors).", excluded=[
        ("yms", "Wardrobe (4+ door)", 2.00, "4+ doors"),
        ("easypeasy", "Wardrobe (large)", 1.80, "Door count not given"),
    ]),
    "desk": _ref(MEDIAN, [
        ("youpack", "Desk [Family]", 1.20),
        ("easypeasy", "Desk", 0.90),
        ("yms", "Desk", 0.70),
        ("quicknslick", "Desk [Study / Home Office]", 0.60),
    ], "Median of 4 removalists.", excluded=[
        ("quicknslick", "Desk - Standard", "0.60 / 1.00", "Same table gives two values"),
        ("quicknslick", "Desk - Corner", 1.50, "Corner desk"),
    ]),
    # ------------------------------------------------------------ dining
    "dining_table_4": _ref(MEDIAN, [
        ("yms", "Dining Table (4-seater)", 1.10),
        ("quicknslick", "Dining Table - 4 Seater", 1.00),
    ], "Median of 2 removalists."),
    "dining_table_6": _ref(MEDIAN, [
        ("easypeasy", "Dining table (6 seat)", 1.20),
        ("yms", "Dining Table (6-seater)", 1.50),
        ("quicknslick", "Dining Table - 6 Seater", 1.50),
        ("movingwithsam", "6 Seater Dining Table", 1.20),
        ("ttmoving", "6-seat dining table", 2.00),
        ("getngo", "Dining Table (6-seater)", 1.20),
    ], "Median of 6 removalists."),
    "dining_table_8": _ref(MEDIAN, [
        ("easypeasy", "Dining table (8+ seat)", 1.50),
        ("yms", "Dining Table (8-seater)", 1.90),
        ("quicknslick", "Dining Table - 8+ Seater", 2.00),
    ], "Median of 3 removalists."),
    "dining_chair": _ref(MEDIAN, [
        ("youpack", "Dining Chairs", 0.14),
        ("easypeasy", "Dining chair", 0.15),
        ("yms", "Dining Chair", 0.20),
        ("quicknslick", "Dining Chairs", 0.20),
    ], "Median of 4 removalists (per chair).", excluded=[
        ("goldline", "6 Dining Chairs", 0.90, "Six chairs together"),
        ("a1", "Dining chair", "0.15–0.25", "Range only"),
    ]),
    "bar_stool": _ref(SINGLE, [
        ("yms", "Bar Stool", 0.15),
    ], "Only one removalist lists a bar stool.", excluded=[
        ("quicknslick", "Stool", 0.15, "Not stated as a bar stool"),
    ]),
    "buffet": _ref(MEDIAN, [
        ("youpack", "Sideboard", 0.85),
        ("easypeasy", "Buffet / sideboard", 1.00),
        ("yms", "Buffet/Sideboard", 1.10),
    ], "Median of 3 removalists.", excluded=[
        ("quicknslick", "Buffet - Small / Large", "1.00 / 2.00", "Split by size"),
    ]),
    "bench_seat": _ref(
        PROVISIONAL,
        reason="No removalist lists an indoor bench seat. Kept the old value; outdoor bench "
        "seats are listed at 0.40 by two removalists (different item).",
        excluded=[
            ("youpack", "Bench Seat [Outdoor]", 0.40, "Outdoor item"),
            ("quicknslick", "Garden Bench Seat", 0.40, "Outdoor item"),
        ],
        value=0.40,
    ),
    # ------------------------------------------------------------ kitchen
    "fridge_single": _ref(SINGLE, [
        ("quicknslick", "Fridge - Small", 0.80),
    ], "Only one removalist lists a small fridge. Label now says small."),
    "fridge_double": _ref(MEDIAN, [
        ("easypeasy", "Fridge (standard)", 1.00),
        ("yms", "Fridge (standard)", 1.30),
    ], "Median of 2 removalists (standard fridge). Label changed from '2 doors / top "
       "freezer' to 'standard'.", excluded=[
        ("goldline", "Fridge", 1.20, "Type not given"),
        ("movingwithsam", "Fridge", 1.20, "Type not given"),
        ("youpack", "Upright Fridge", 1.28, "Type not given"),
        ("quicknslick", "Fridge - Medium", 1.00, "Split by size, not type"),
        ("a1", "Fridge", "0.9–1.4", "Range only"),
    ], outer=[("ao_midea_fridge", "Midea MTM400W 400L top mount", "171.2 × 69.6 × 68.5 cm", 0.82)]),
    "fridge_large": _ref(MEDIAN, [
        ("easypeasy", "Fridge (French door / large)", 1.40),
        ("yms", "Fridge (large/double door)", 1.80),
        ("quicknslick", "Fridge - 2 Door", 1.60),
    ], "Median of 3 removalists (French / double door).", excluded=[
        ("ttmoving", "Large fridge", 1.30, "Door type not given"),
        ("quicknslick", "Fridge - Large", 1.20, "Size split; their 2-door is listed separately"),
        ("getngo", "Large Fridge/Freezer", 1.20, "Door type not given"),
    ], outer=[("fragile", "French door fridge (measuring example)", "172 × 79 × 69 cm", 0.97)]),
    "bar_fridge": _ref(MEDIAN, [
        ("youpack", "Bar Fridge", 0.30),
        ("easypeasy", "Bar fridge", 0.40),
        ("yms", "Bar Fridge", 0.40),
        ("quicknslick", "Bar Fridge", 0.25),
    ], "Median of 4 removalists."),
    "freezer": _ref(MEDIAN, [
        ("youpack", "Chest Freezer", 1.00),
        ("yms", "Chest Freezer", 1.20),
    ], "Median of 2 removalists. Item is now chest freezer; upright freezer is separate."),
    "freezer_upright": _ref(MEDIAN, [
        ("youpack", "Upright Freezer", 1.00),
        ("yms", "Upright Freezer", 1.00),
    ], "Median of 2 removalists."),
    "microwave": _ref(MEDIAN, [
        ("youpack", "Microwave", 0.15),
        ("easypeasy", "Microwave", 0.15),
        ("quicknslick", "Microwave", 0.15),
    ], "Median of 3 removalists.", excluded=[
        ("yms", "Microwave/Small Appliances (boxed)", 0.20, "Boxed, with other appliances"),
    ]),
    "dishwasher": _ref(MEDIAN, [
        ("youpack", "Dishwasher", 0.28),
        ("easypeasy", "Dishwasher", 0.50),
        ("yms", "Dishwasher", 0.50),
        ("quicknslick", "Dishwasher", 0.50),
    ], "Median of 4 removalists."),
    "kitchen_cabinet": _ref(SINGLE, [
        ("yms", "Pantry Cupboard/Kitchen Hutch", 0.90),
    ], "Only one removalist lists a pantry cupboard.", excluded=[
        ("youpack", "Cupboard [Laundry]", 1.00, "Laundry cupboard"),
        ("quicknslick", "Cupboard [Laundry]", 1.00, "Laundry cupboard"),
    ]),
    # ------------------------------------------------------------ laundry
    "washing_machine": _ref(MEDIAN, [
        ("youpack", "Washing Machine", 0.60),
        ("easypeasy", "Washing machine", 0.60),
        ("yms", "Washing Machine", 0.70),
        ("quicknslick", "Washing Machine", 0.90),
        ("ttmoving", "Washing machine", 0.50),
        ("getngo", "Washing Machine", 0.50),
    ], "Median of 6 removalists. Front/top loader not split by any source.", excluded=[
        ("a1", "Washing machine", "0.5–0.8", "Range only"),
    ], outer=[("ao_fp_washer", "F&P WH80F60W1 8kg front loader", "84 × 60 × 59 cm", 0.30)]),
    "dryer": _ref(MEDIAN, [
        ("youpack", "Dryer", 0.35),
        ("easypeasy", "Clothes dryer", 0.50),
        ("yms", "Dryer", 0.70),
        ("quicknslick", "Dryer", 0.70),
    ], "Median of 4 removalists.", excluded=[("a1", "Dryer", "0.3–0.6", "Range only")]),
    "ironing_board": _ref(MEDIAN, [
        ("youpack", "Ironing Board", 0.10),
        ("yms", "Ironing Board", 0.15),
        ("quicknslick", "Ironing Board", 0.10),
    ], "Median of 3 removalists."),
    "laundry_basket": _ref(MEDIAN, [
        ("youpack", "Laundry Basket", 0.10),
        ("yms", "Laundry Hamper", 0.10),
        ("quicknslick", "Clothes Basket", 0.10),
    ], "Median of 3 removalists."),
    # ------------------------------------------------------------ study
    "office_chair": _ref(MEDIAN, [
        ("youpack", "Office Chair", 0.20),
        ("easypeasy", "Office chair", 0.40),
        ("yms", "Office Chair", 0.30),
        ("quicknslick", "Office Chair", 0.30),
    ], "Median of 4 removalists."),
    "filing_cabinet": _ref(MEDIAN, [
        ("easypeasy", "Filing cabinet", 0.50),
        ("yms", "Filing Cabinet", 0.60),
    ], "Median of 2 removalists (drawer count not given).", excluded=[
        ("youpack", "Filing Cabinet 2/3/4 drawer", "0.30 / 0.40 / 0.60", "Split by drawers"),
        ("quicknslick", "Filing Cabinet - 2/3/4 Drawer", "0.25 / 0.35 / 0.45", "Split by drawers"),
    ]),
    "computer": _ref(MEDIAN, [
        ("youpack", "Computer", 0.25),
        ("easypeasy", "Computer / monitor (each)", 0.20),
        ("yms", "Computer/Monitor (boxed)", 0.20),
    ], "Median of 3 removalists."),
    "printer": _ref(SINGLE, [
        ("yms", "Printer/Scanner (boxed)", 0.15),
    ], "Only one removalist lists a printer."),
    # ------------------------------------------------------------ storage
    "shelf_small": _ref(
        PROVISIONAL,
        reason="No removalist splits shelving units by size. Kept the old value.",
        excluded=[
            ("easypeasy", "Office shelving", 0.70, "Size not given"),
            ("yms", "Garage Shelving Unit", 0.70, "Size not given"),
        ],
        value=0.40,
    ),
    "shelf_large": _ref(
        PROVISIONAL,
        reason="No removalist splits shelving units by size. Kept the old value.",
        excluded=[("youpack", "Shelf [Garage]", 1.00, "Size not given")],
        value=0.80,
    ),
    "plastic_tub": _ref(SINGLE, [
        ("yms", "Storage Tub (each)", 0.06),
    ], "Only one removalist lists a storage tub.", excluded=[
        ("youpack", "Plastic Boxes", 0.20, "Different item name"),
    ]),
    # ------------------------------------------------------------ outdoor
    "outdoor_table": _ref(
        PROVISIONAL,
        reason="Removalists list outdoor tables only with a seat count or with chairs. Kept the old value.",
        excluded=[
            ("quicknslick", "Outdoor Dining Table - 6 / 8+ Seater", "1.50 / 2.00", "Seat count given"),
            ("quicknslick", "Outdoor Lounge Table", 0.40, "Lounge (low) table"),
            ("easypeasy", "Outdoor table + chairs", 1.50, "Includes chairs"),
        ],
        value=0.90,
    ),
    "outdoor_chair": _ref(SINGLE, [
        ("quicknslick", "Outdoor Dining Chairs", 0.20),
    ], "Only one removalist lists a standard outdoor chair.", excluded=[
        ("quicknslick", "Outdoor Chairs - Stackable", 0.10, "Stackable"),
        ("youpack", "Stackable Chair", 0.30, "Stackable"),
    ]),
    "outdoor_lounge": _ref(SINGLE, [
        ("yms", "Outdoor Lounge/Sofa Setting", 2.00),
    ], "Only one removalist lists an outdoor lounge set.", excluded=[
        ("quicknslick", "Outdoor Lounge - 3 Seater", 2.00, "A single 3-seater, not a set"),
    ]),
    "sun_lounger": _ref(SINGLE, [
        ("yms", "Sun Lounger/Deck Chair", 0.30),
    ], "Only one removalist lists a sun lounger."),
    "bbq": _ref(MEDIAN, [
        ("easypeasy", "BBQ", 0.70),
        ("yms", "BBQ", 0.60),
    ], "Median of 2 removalists.", excluded=[
        ("youpack", "BBQ 2/3/4 Burner", "0.40 / 0.50 / 0.75", "Split by burners"),
    ]),
    "pot_plant_small": _ref(
        PROVISIONAL,
        reason="No per-pot value for a small pot. Kept the old value.",
        excluded=[
            ("youpack", "Pots & Plants (Small)", 0.14, "Plural; per pot not clear"),
            ("quicknslick", "Pot Plants - Small", 0.50, "Plural; per pot not clear"),
        ],
        value=0.10,
    ),
    "pot_plant_large": _ref(SINGLE, [
        ("yms", "Large Garden Pot (each)", 0.15),
    ], "Only one removalist gives a per-pot value.", excluded=[
        ("youpack", "Pots & Plants (Large)", 1.00, "Plural; per pot not clear"),
    ]),
    "bicycle": _ref(MEDIAN, [
        ("youpack", "Adults Bike", 0.40),
        ("easypeasy", "Bicycle", 0.40),
        ("yms", "Bicycle", 0.30),
        ("quicknslick", "Bicycle - Adult", 0.40),
        ("movingwithsam", "Bicycle", 0.50),
    ], "Median of 5 removalists.", excluded=[("a1", "Bicycle", "0.4–0.8", "Range only")]),
    "lawn_mower": _ref(MEDIAN, [
        ("youpack", "Lawn Mower", 0.30),
        ("easypeasy", "Lawn mower", 0.40),
        ("yms", "Lawnmower", 0.50),
        ("quicknslick", "Lawn Mower", 0.35),
    ], "Median of 4 removalists.", excluded=[
        ("youpack", "Mower (Drained)", 0.15, "Drained mower listed separately"),
    ]),
    "tool_box": _ref(SINGLE, [
        ("quicknslick", "Tool Box", 0.15),
    ], "Only one removalist lists a plain tool box.", excluded=[
        ("youpack", "Tool Chest Small", 0.10, "Tool chest"),
        ("easypeasy", "Toolbox / power tools", 0.30, "Includes power tools"),
    ]),
    "treadmill": _ref(SINGLE, [
        ("yms", "Treadmill", 1.00),
    ], "Only one removalist lists a treadmill without a condition.", excluded=[
        ("youpack", "Treadmill - flat", 1.20, "Flat (not folded) condition"),
    ]),
    "exercise_bike": _ref(MEDIAN, [
        ("youpack", "Exercise bike", 0.40),
        ("yms", "Exercise Bike", 0.60),
        ("quicknslick", "Exercise Bike", 0.50),
    ], "Median of 3 removalists."),
    # ------------------------------------------------------------ boxes
    "box_small": _ref(MEDIAN, [
        ("easypeasy", "Small box", 0.05),
        ("yms", "Small Box", 0.05),
    ], "Median of 2 removalists (small box).", excluded=[
        ("yms", "Book Box (heavy-duty, small)", 0.06, "Book box listed separately"),
        ("quicknslick", "Book Boxes", 0.10, "Book box"),
        ("youpack", "Book Carton", "0.05 / 0.10", "Same table gives two values"),
    ], outer=[("findamover", "Book box", "40.3 × 30.1 × 33 cm (40 L)", 0.04)]),
    "box_medium": _ref(MEDIAN, [
        ("easypeasy", "Medium box", 0.08),
        ("yms", "Medium Box", 0.08),
        ("quicknslick", "Medium Box - Approx 50 Litres", 0.08),
    ], "Median of 3 removalists.", outer=[
        ("findamover", "Standard carton", "40.6 × 29.8 × 43.1 cm (52 L)", 0.05),
    ]),
    "box_large": _ref(MEDIAN, [
        ("easypeasy", "Large box (books, kitchen)", 0.12),
        ("yms", "Large Box", 0.15),
        ("quicknslick", "Large Box - Approx 100 Litres", 0.15),
    ], "Median of 3 removalists.", excluded=[
        ("youpack", "Standard Removal", 0.15, "Different item name"),
    ], outer=[
        ("findamover", "Tea chest", "43.1 × 40.6 × 59.6 cm (104 L)", 0.10),
    ]),
    "box_wardrobe": _ref(MEDIAN, [
        ("easypeasy", "Wardrobe box (with clothes)", 0.35),
        ("yms", "Wardrobe Box (hanging clothes)", 0.30),
        ("quicknslick", "Wardrobe Boxes", 0.35),
    ], "Median of 3 removalists.", excluded=[
        ("youpack", "Port-a-robes", 0.30, "Different item name"),
    ], outer=[("findamover", "Port-a-robe", "49 × 59.7 × 110 cm (322 L)", 0.32)]),
    "suitcase": _ref(MEDIAN, [
        ("youpack", "Suitcase", 0.10),
        ("quicknslick", "Suitcases", 0.15),
    ], "Median of 2 removalists.", excluded=[
        ("easypeasy", "Suitcase (large)", 0.20, "Large size"),
    ]),
    "bag": _ref(
        PROVISIONAL,
        reason="No removalist lists a generic bag/bundle. Kept the old value.",
        excluded=[
            ("youpack", "Striped bags", 0.10, "Striped bag"),
            ("quicknslick", "Striped Bags", 0.15, "Striped bag"),
        ],
        value=0.08,
    ),
}


def _round(value: float) -> float:
    return round(float(value) + 1e-12, 3)


def researched_m3(key: str) -> float:
    """Researched default m³ for an item key (raises KeyError for unknown keys)."""
    ref = REFS[key]
    basis = ref["basis"]
    if basis == MEDIAN:
        return _round(median(value for _src, _label, value in ref["refs"]))
    if basis == SINGLE:
        return _round(ref["refs"][0][2])
    if basis == DERIVED:
        whole, mattress = ref["derived_from"]
        return _round(researched_m3(whole) - researched_m3(mattress))
    return _round(ref["value"])


def source_payload(source_id: str) -> Dict[str, str]:
    name, url, kind, note = SOURCES[source_id]
    return {"id": source_id, "name": name, "url": url, "kind": kind, "note": note}
