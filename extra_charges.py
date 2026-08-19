"""Extra charge line items on bookings."""

from typing import Any, Dict, List, Tuple


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
