"""Extra charge line items on bookings."""

import re
from typing import Any, Dict, List, Tuple

_BREAK_MINUTES_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*min\s+break",
    re.IGNORECASE,
)


def _extra_charge_row_is_empty(description: str, qty_raw: str, price_raw: str) -> bool:
    """True when the user did not enter a description or unit price."""
    desc = (description or "").strip()
    price = (price_raw or "").strip()
    if desc:
        return False
    if not price:
        return True
    try:
        return float(price) == 0
    except (TypeError, ValueError):
        return False


def parse_extra_charges_from_form(form: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    descriptions = _field_list(form, "extra_description")
    quantities = _field_list(form, "extra_quantity")
    unit_prices = _field_list(form, "extra_unit_price")
    errors: List[str] = []
    items: List[Dict[str, Any]] = []

    row_count = max(len(descriptions), len(quantities), len(unit_prices))
    for index in range(row_count):
        description = descriptions[index] if index < len(descriptions) else ""
        qty_raw = quantities[index] if index < len(quantities) else "1"
        price_raw = unit_prices[index] if index < len(unit_prices) else "0"
        description = (description or "").strip()
        qty_raw = (qty_raw or "").strip()
        price_raw = (price_raw or "").strip()

        if _extra_charge_row_is_empty(description, qty_raw, price_raw):
            continue
        if not description:
            errors.append("Extra charge row {0}: description is required.".format(index + 1))
            continue
        try:
            quantity = float(qty_raw or "1")
            if quantity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Extra charge row {0}: quantity must be greater than zero.".format(index + 1))
            continue
        try:
            unit_price = float(price_raw or "0")
        except (TypeError, ValueError):
            errors.append(
                "Extra charge row {0}: unit price must be a number.".format(index + 1)
            )
            continue
        items.append(
            {
                "description": description,
                "quantity": round(quantity, 2),
                "unit_price": round(unit_price, 2),
            }
        )
    return items, errors


def _field_list(form: Any, name: str) -> List[str]:
    if hasattr(form, "getlist"):
        return list(form.getlist(name))
    raw = form.get(name, [])
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if raw:
        return [str(raw)]
    return []


def charge_line_total(item: Dict[str, Any]) -> float:
    return round(float(item.get("quantity") or 0) * float(item.get("unit_price") or 0), 2)


def charges_gross_total(items: List[Dict[str, Any]]) -> float:
    return round(sum(charge_line_total(item) for item in items), 2)


def break_deduction_unit_price(hourly_rate: Any, break_minutes: Any) -> float:
    """GST-inclusive deduction: hourly_rate × minutes / 60 × -1."""
    try:
        rate = float(hourly_rate or 0)
        minutes = float(break_minutes or 0)
    except (TypeError, ValueError):
        return 0.0
    if rate < 0 or minutes <= 0:
        return 0.0
    return round(rate * minutes / 60.0 * -1, 2)


def break_deduction_description(break_minutes: Any) -> str:
    try:
        minutes = float(break_minutes or 0)
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0:
        return "Break deduction"
    if minutes == int(minutes):
        label = str(int(minutes))
    else:
        label = "{0:g}".format(minutes)
    return "{0} min break deduction".format(label)


def is_break_deduction_charge(description: Any) -> bool:
    """True when an extra charge line is an unpaid break deduction."""
    text = str(description or "").strip().lower()
    return "break" in text and "deduction" in text


def break_minutes_from_charge(
    item: Dict[str, Any], hourly_rate: Any = None
) -> float:
    """Minutes deducted for one break extra-charge row (same source as Invoice)."""
    desc = str(item.get("description") or "").strip()
    if not is_break_deduction_charge(desc):
        return 0.0
    try:
        qty = float(item.get("quantity") or 1)
    except (TypeError, ValueError):
        qty = 1.0
    if qty <= 0:
        return 0.0
    match = _BREAK_MINUTES_RE.match(desc)
    if match:
        return float(match.group(1)) * qty
    try:
        unit = float(item.get("unit_price") or 0)
        rate = float(hourly_rate or 0)
    except (TypeError, ValueError):
        return 0.0
    if unit >= 0 or rate <= 0:
        return 0.0
    return abs(unit) * qty / rate * 60.0


def break_hours_from_booking(booking: Dict[str, Any]) -> float:
    """Unpaid break hours stored on booking extra charges (not from invoice totals)."""
    charges = booking.get("extra_charges")
    if charges is None and booking.get("id"):
        import database as db

        charges = db.list_extra_charges(int(booking["id"]))
    total_minutes = 0.0
    rate = booking.get("hourly_rate")
    for item in charges or []:
        total_minutes += break_minutes_from_charge(item, rate)
    if total_minutes <= 0:
        return 0.0
    return round(total_minutes / 60.0, 2)
