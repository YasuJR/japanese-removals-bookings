"""WG Outdoor Life delivery orders (WG Orders page, /wg-orders).

WG sends batches of delivery jobs, often several on one day. Each order is
saved as an ordinary booking so Calendar, Staff Portal, Weekly Schedule, Paid
Hours, Search and the Dashboard pick it up with no special case. What marks
it as WG:

- bookings.source = "wg_outdoor_life" (wg_jobs.WG_ORDER_SOURCE)
- customer_name = "WG - <customer>", so the existing name-based WG checks
  (Dashboard WG OUTDOOR LIFE unpaid section) and every list show it as WG
- wg_products (multi-line, kept as typed), wg_reference ("REX 3277"),
  wg_payment_amount, wg_payment_type (COD / Confirmed / Invoice / Other, or WG's own word such as "YASU")

The payment amount is the invoice total: hourly_rate 0 plus one extra charge
"WG Outdoor Life delivery — <reference>" (see invoice_labour_lines, which drops
the empty labour line for WG orders). Delivery address = the order address;
pickup_address is "WG Outdoor Life" (their store/warehouse).

Every entry route builds the same order dict (ORDER_KEYS) and goes through
validate() → find_duplicates() → save_order(). The page form uses
parse_form_rows(); a CSV import or AI parse later only has to build the dicts.

WG orders never send customer SMS, payment reminders, review requests, or
create Xero invoices automatically (wg_jobs.is_wg_order guards). Google
Calendar sync still runs.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import database as db
import double_booking
import wg_jobs
from booking_times import duration_hours_from_times, format_time_12h, normalize_time_input
from crew import active_crew_names, crew_from_storage, crew_storage_value

SOURCE = wg_jobs.WG_ORDER_SOURCE
NAME_PREFIX = "WG - "
PICKUP_LABEL = "WG Outdoor Life"
CHARGE_PREFIX = "WG Outdoor Life delivery"
PAYMENT_TYPES = ("COD", "Confirmed", "Invoice", "Other")  # suggestions; WG also writes e.g. "YASU"
PAYMENT_TYPE_MAX = 30
DEFAULT_STATUS = "Confirmed"
FORM_PREFIX = "orders"

ORDER_KEYS = (
    "customer",
    "products",
    "move_date",
    "start_time",
    "finish_time",
    "address",
    "phone",
    "payment_amount",
    "payment_type",
    "reference",
    "notes",
    "crew",
    "truck",
)

_REX_RE = re.compile(r"\b(?:rex\s*)?(\d{3,6})\b", re.IGNORECASE)
_NAME_PREFIX_RE = re.compile(r"^\s*wg\s*(?:outdoor\s+life)?\s*[-–—:]?\s*", re.IGNORECASE)


# --- names -------------------------------------------------------------------------


def booking_name(customer: Any) -> str:
    return NAME_PREFIX + plain_customer(customer)


def plain_customer(name: Any) -> str:
    """Customer name without the "WG - " prefix."""
    text = str(name or "").strip()
    stripped = _NAME_PREFIX_RE.sub("", text, count=1).strip()
    return stripped or text


def reference_numbers(reference: Any) -> set:
    """{"3277"} for "REX 3277"; {"1649", "2359"} for "REX 1649 AND REX 2359"."""
    return set(_REX_RE.findall(str(reference or "")))


def _norm(text: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(text or "").lower()).split())


def normalize_products(value: Any) -> str:
    """Keep the typed lines; only unify line endings and trim the ends."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip("\n")


# --- parsing -----------------------------------------------------------------------


def empty_order(move_date: str = "") -> Dict[str, Any]:
    order: Dict[str, Any] = {key: "" for key in ORDER_KEYS}
    order.update({"move_date": move_date, "crew": [], "payment_type": "Invoice"})
    return order


def normalize_payment_type(value: Any) -> str:
    """"cod" → "COD", "confirmed" → "Confirmed"; other words (e.g. "YASU") kept as written."""
    text = " ".join(str(value or "").split())
    for known in PAYMENT_TYPES:
        if text.lower() == known.lower():
            return known
    return text


def _float(value: Any) -> Optional[float]:
    text = str(value or "").replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def clean_order(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise one order dict (from the form, a CSV row, or a parser)."""
    crew = raw.get("crew") or []
    if isinstance(crew, str):
        crew = crew_from_storage(crew)
    allowed = active_crew_names()
    order = {
        "customer": plain_customer(raw.get("customer")),
        "products": normalize_products(raw.get("products")),
        "move_date": str(raw.get("move_date") or "").strip()[:10],
        "start_time": normalize_time_input(raw.get("start_time")),
        "finish_time": normalize_time_input(raw.get("finish_time")),
        "address": " ".join(str(raw.get("address") or "").split()),
        "phone": str(raw.get("phone") or "").strip(),
        "payment_amount": _float(raw.get("payment_amount")),
        "payment_amount_raw": str(raw.get("payment_amount") or "").strip(),
        "payment_type": normalize_payment_type(raw.get("payment_type")),
        "reference": " ".join(str(raw.get("reference") or "").split()),
        "notes": str(raw.get("notes") or "").replace("\r\n", "\n").strip(),
        "crew": [name for name in crew if name in allowed],
        "truck": str(raw.get("truck") or "").strip(),
        "booking_id": raw.get("booking_id"),
        "override": bool(raw.get("override")),
        "force_duplicate": bool(raw.get("force_duplicate")),
        "row": raw.get("row"),
    }
    return order


def is_blank(raw: Dict[str, Any]) -> bool:
    """A form row the user never filled in (date / payment type defaults don't count)."""
    keys = ("customer", "products", "start_time", "finish_time", "address", "phone", "payment_amount", "reference", "notes")
    return not any(str(raw.get(key) or "").strip() for key in keys)


def parse_form_rows(form: Any) -> List[Dict[str, Any]]:
    """
    Orders from the WG Orders form. Fields are named "orders-<n>-<key>";
    crew checkboxes repeat "orders-<n>-crew". Rows come back in page order.
    """
    indexes = set()
    pattern = re.compile(r"^{0}-(\d+)-".format(FORM_PREFIX))
    for key in form.keys():
        match = pattern.match(key)
        if match:
            indexes.add(int(match.group(1)))
    rows = []
    for n in sorted(indexes):
        prefix = "{0}-{1}-".format(FORM_PREFIX, n)
        raw: Dict[str, Any] = {"row": n}
        for key in ORDER_KEYS:
            if key == "crew":
                raw["crew"] = form.getlist(prefix + "crew") if hasattr(form, "getlist") else form.get(prefix + "crew") or []
            else:
                raw[key] = form.get(prefix + key, "")
        raw["booking_id"] = form.get(prefix + "booking_id") or None
        raw["override"] = form.get(prefix + "override") == "on"
        raw["force_duplicate"] = form.get(prefix + "force_duplicate") == "on"
        rows.append(raw)
    return rows


# --- validation ----------------------------------------------------------------------


def validate(order: Dict[str, Any]) -> List[str]:
    errors = []
    if not order["customer"]:
        errors.append("Customer name is required.")
    if not order["move_date"] or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", order["move_date"]):
        errors.append("Date is required.")
    if not order["start_time"]:
        errors.append("Start time is required.")
    if order["finish_time"] and order["start_time"]:
        if duration_hours_from_times(order["start_time"], order["finish_time"]) is None:
            errors.append("Finish time must be after start time.")
    if not order["address"]:
        errors.append("Address is required.")
    if order["payment_amount_raw"] and order["payment_amount"] is None:
        errors.append("Payment amount must be a number.")
    elif order["payment_amount"] is not None and order["payment_amount"] < 0:
        errors.append("Payment amount can't be negative.")
    if len(order["payment_type"]) > PAYMENT_TYPE_MAX:
        errors.append("Payment type is too long (max {0} characters).".format(PAYMENT_TYPE_MAX))
    return errors


def find_duplicates(order: Dict[str, Any], *, exclude_booking_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    WG bookings on the same date that look like the same order: same customer,
    same address, or a shared REX number. A warning only; the owner can save anyway.
    """
    if not order.get("move_date"):
        return []
    name = _norm(order.get("customer"))
    address = _norm(order.get("address"))
    refs = reference_numbers(order.get("reference"))
    matches = []
    for row in db.list_by_date(order["move_date"]):
        other = dict(row)
        if exclude_booking_id is not None and int(other["id"]) == int(exclude_booking_id):
            continue
        if not wg_jobs.is_wg_outdoor_life(other):
            continue
        reasons = []
        if name and _norm(plain_customer(other.get("customer_name"))) == name:
            reasons.append("same customer")
        if address and _norm(other.get("delivery_address")) == address:
            reasons.append("same address")
        shared = refs & reference_numbers(other.get("wg_reference"))
        if shared:
            reasons.append("same REX {0}".format(", ".join(sorted(shared))))
        if reasons:
            matches.append(
                {
                    "id": int(other["id"]),
                    "customer_name": other.get("customer_name") or "",
                    "time_label": format_time_12h(normalize_time_input(other.get("start_time"))) or "Time TBC",
                    "reasons": reasons,
                }
            )
    return matches


# --- booking conversion --------------------------------------------------------------


def charge_description(reference: str) -> str:
    return "{0} — {1}".format(CHARGE_PREFIX, reference) if reference else CHARGE_PREFIX


def booking_data(order: Dict[str, Any]) -> Dict[str, Any]:
    """Booking fields (create_booking / update_booking / conflict checks)."""
    crew = list(order.get("crew") or [])
    duration = ""
    if order["start_time"] and order["finish_time"]:
        hours = duration_hours_from_times(order["start_time"], order["finish_time"])
        duration = "{0:g}".format(hours) if hours else ""
    return {
        "customer_name": booking_name(order["customer"]),
        "phone": order["phone"],
        "email": "",
        "pickup_address": PICKUP_LABEL,
        "delivery_address": order["address"],
        "move_date": order["move_date"],
        "num_movers": max(1, len(crew)) if crew else 2,
        "notes": order["notes"],
        "start_time": order["start_time"],
        "finish_time": order["finish_time"],
        "duration_hours": duration,
        "crew": crew,
        "crew_csv": crew_storage_value(crew),
        "truck_assigned": order["truck"],
        "status": DEFAULT_STATUS,
    }


def wg_fields(order: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": SOURCE,
        "wg_products": order["products"],
        "wg_reference": order["reference"],
        "wg_payment_amount": order["payment_amount"],
        "wg_payment_type": order["payment_type"],
    }


def _sync_payment_charge(booking_id: int, order: Dict[str, Any]) -> None:
    """One "WG Outdoor Life delivery" extra charge = the payment amount. Other charges stay."""
    others = [
        item
        for item in db.list_extra_charges(booking_id)
        if not str(item.get("description") or "").startswith(CHARGE_PREFIX)
    ]
    items = []
    if order["payment_amount"] is not None:
        items.append(
            {"description": charge_description(order["reference"]), "quantity": 1, "unit_price": order["payment_amount"]}
        )
    db.replace_extra_charges(booking_id, items + others)


def conflict_check(order: Dict[str, Any], booking_id: Optional[int] = None) -> Tuple[List[str], List[Dict[str, Any]], bool]:
    """Existing double-booking rules (different trucks never conflict)."""
    return double_booking.validate_save(
        booking_data(order), booking_id=booking_id, override_confirmed=order.get("override", False)
    )


def crew_warnings(order: Dict[str, Any], booking_id: Optional[int] = None) -> List[str]:
    from resource_conflicts import find_resource_conflict_warnings

    data = booking_data(order)
    payload = {
        "id": booking_id if booking_id is not None else -1,
        "move_date": data["move_date"],
        "start_time": data["start_time"],
        "finish_time": data["finish_time"],
        "duration_hours": data["duration_hours"],
        "status": data["status"],
        "crew": data["crew"],
        "truck_assigned": data["truck_assigned"],
        "customer_name": data["customer_name"],
    }
    return find_resource_conflict_warnings(payload, exclude_booking_id=booking_id)


def _after_save(booking_id: int) -> List[str]:
    """Google Calendar + profit. No SMS, payment link or Xero for WG orders."""
    import booking_profit
    import services
    from integrations import google_calendar

    messages = []
    row = db.get_booking(booking_id)
    if row:
        msg = google_calendar.sync_booking_to_calendar(services.booking_to_dict(row))
        if msg:
            messages.append(msg)
    booking_profit.recalculate_and_save(booking_id)
    return messages


def save_order(order: Dict[str, Any]) -> Tuple[Optional[int], Dict[str, Any]]:
    """
    Create (or, with booking_id, update) the booking for one order.
    Returns (booking_id or None, result) — result has errors, duplicates,
    conflicts, warnings, messages. Nothing is written when there are errors,
    an unconfirmed duplicate, or an unconfirmed time conflict.
    """
    booking_id = int(order["booking_id"]) if order.get("booking_id") else None
    result: Dict[str, Any] = {"errors": validate(order), "duplicates": [], "conflicts": [], "warnings": [], "messages": []}
    if result["errors"]:
        return None, result
    result["duplicates"] = find_duplicates(order, exclude_booking_id=booking_id)
    if result["duplicates"] and not order.get("force_duplicate"):
        result["errors"].append('Possible duplicate WG order — tick "Save anyway" to save it.')
        return None, result
    conflict_errors, conflicts, _override = conflict_check(order, booking_id)
    result["conflicts"] = conflicts
    if conflict_errors:
        result["errors"].extend(conflict_errors)
        return None, result
    result["warnings"] = crew_warnings(order, booking_id)

    data = booking_data(order)
    if booking_id is None:
        booking_id = db.create_booking(
            customer_name=data["customer_name"],
            phone=data["phone"],
            email="",
            pickup_address=data["pickup_address"],
            delivery_address=data["delivery_address"],
            move_date=data["move_date"],
            num_movers=data["num_movers"],
            notes=data["notes"],
            start_time=data["start_time"],
            finish_time=data["finish_time"],
            duration_hours=data["duration_hours"],
            crew=data["crew_csv"],
            hourly_rate=0.0,
            callout_fee=0.0,
            callout_minutes=0,
            gst_enabled=1,
            payment_status="Unpaid",
            invoice_status="",
            status=data["status"],
        )
    else:
        row = dict(db.get_booking(booking_id))
        db.update_booking(
            booking_id=booking_id,
            customer_name=data["customer_name"],
            phone=data["phone"],
            email=row.get("email") or "",
            pickup_address=row.get("pickup_address") or PICKUP_LABEL,
            delivery_address=data["delivery_address"],
            move_date=data["move_date"],
            num_movers=data["num_movers"],
            notes=data["notes"],
            start_time=data["start_time"],
            finish_time=data["finish_time"],
            duration_hours=data["duration_hours"],
            crew=data["crew_csv"],
            hourly_rate=float(row.get("hourly_rate") or 0),
            callout_fee=float(row.get("callout_fee") or 0),
            callout_minutes=row.get("callout_minutes"),
            gst_enabled=int(row.get("gst_enabled") if row.get("gst_enabled") is not None else 1),
            payment_status=row.get("payment_status") or "Unpaid",
            invoice_status=row.get("invoice_status") or "",
            status=row.get("status") or DEFAULT_STATUS,
        )
    db.save_booking_wg_fields(booking_id, wg_fields(order))
    db.update_booking_integration_fields(booking_id, {"truck_assigned": data["truck_assigned"]})
    if conflicts and order.get("override"):
        from datetime import datetime

        db.update_booking_integration_fields(
            booking_id, {"double_booking_override_at": datetime.utcnow().isoformat(timespec="seconds")}
        )
    _sync_payment_charge(booking_id, order)
    result["messages"] = _after_save(booking_id)
    return booking_id, result


def save_orders(raw_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Save every filled row in order. Rows with problems are returned unsaved."""
    outcomes = []
    for raw in raw_rows:
        if is_blank(raw) and not raw.get("booking_id"):
            continue
        order = clean_order(raw)
        booking_id, result = save_order(order)
        outcomes.append({"order": order, "booking_id": booking_id, **result})
    return outcomes


# --- reading back --------------------------------------------------------------------


def order_from_booking(booking: Any) -> Dict[str, Any]:
    b = dict(booking)
    amount = b.get("wg_payment_amount")
    return {
        "customer": plain_customer(b.get("customer_name")),
        "products": b.get("wg_products") or "",
        "move_date": str(b.get("move_date") or "")[:10],
        "start_time": normalize_time_input(b.get("start_time")),
        "finish_time": normalize_time_input(b.get("finish_time")),
        "address": b.get("delivery_address") or "",
        "phone": b.get("phone") or "",
        "payment_amount": amount,
        "payment_amount_raw": "{0:.2f}".format(float(amount)) if amount not in (None, "") else "",
        "payment_type": b.get("wg_payment_type") or "",
        "reference": b.get("wg_reference") or "",
        "notes": b.get("notes") or "",
        "crew": crew_from_storage(b.get("crew")),
        "truck": b.get("truck_assigned") or "",
        "booking_id": b.get("id"),
        "override": False,
        "force_duplicate": False,
    }


def format_amount(amount: Any) -> str:
    if amount in (None, ""):
        return ""
    value = float(amount)
    return "${0:,.0f}".format(value) if value == int(value) else "${0:,.2f}".format(value)


def time_range_label(start: Any, finish: Any) -> str:
    start_hm = normalize_time_input(start)
    finish_hm = normalize_time_input(finish)
    if not start_hm:
        return "Time TBC"
    if not finish_hm:
        return format_time_12h(start_hm)
    return "{0} – {1}".format(format_time_12h(start_hm), format_time_12h(finish_hm))


def staff_fields(booking: Any) -> Dict[str, Any]:
    """WG keys for Staff Portal / calendar payloads (is_wg False for normal bookings)."""
    b = dict(booking) if booking is not None else {}
    if not wg_jobs.is_wg_order(b):
        return {"is_wg": False}
    return {
        "is_wg": True,
        "wg_customer": plain_customer(b.get("customer_name")),
        "wg_products": b.get("wg_products") or "",
        "wg_reference": b.get("wg_reference") or "",
        "wg_payment_display": " · ".join(
            part for part in (format_amount(b.get("wg_payment_amount")), b.get("wg_payment_type") or "") if part
        ),
    }


def day_orders(move_date: str) -> List[Dict[str, Any]]:
    """WG bookings on one date, by start time (WG Daily View)."""
    rows = [dict(row) for row in db.list_by_date(move_date)]
    wg_rows = [row for row in rows if wg_jobs.is_wg_outdoor_life(row)]
    wg_rows.sort(key=lambda row: (normalize_time_input(row.get("start_time")) or "99:99", int(row["id"])))
    out = []
    for row in wg_rows:
        out.append(
            {
                "id": int(row["id"]),
                "time_range": time_range_label(row.get("start_time"), row.get("finish_time")),
                "customer": plain_customer(row.get("customer_name")),
                "payment": format_amount(row.get("wg_payment_amount")),
                "payment_type": row.get("wg_payment_type") or "",
                "reference": row.get("wg_reference") or "",
                "address": row.get("delivery_address") or "",
                "crew": ", ".join(crew_from_storage(row.get("crew"))) or "Unassigned",
                "truck": row.get("truck_assigned") or "No truck",
                "status": row.get("status") or "",
                "is_order": wg_jobs.is_wg_order(row),
            }
        )
    return out
