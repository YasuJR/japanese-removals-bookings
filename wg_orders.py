"""WG Outdoor Life delivery days (WG Orders page, /wg-orders).

WG sends a sheet of deliveries for a day; one crew and one truck run them in
order. So WG is kept per day:

- One booking per WG Day: source = "wg_outdoor_life" (wg_jobs.WG_ORDER_SOURCE),
  customer_name "WG Outdoor Life", crew / truck / start / finish / status /
  invoice exactly like any booking, plus bookings.wg_stop_count (not NULL =
  WG Day, see wg_jobs.is_wg_day). Dashboard, Calendar and the Staff Portal
  therefore show one "WG" job for the day, never the WG customers.
- Its delivery stops live in the wg_stops table (customer, products, start /
  finish, address, phone, payment amount / type, REX reference, notes), kept
  sorted by start time.
- Billing is the WG Day booking's normal hourly invoice (as the hand-made "WG"
  day bookings always were). Stop payments are information for the crew
  (COD to collect etc.) and never become invoice lines or Dashboard rows.

Every entry route builds the same day dict (empty_day / parse_day_form /
the import) and goes through clean_day() → validate_day() → duplicates →
save_day(). Older per-customer WG bookings (source set, wg_stop_count NULL,
"WG - <customer>") keep working and can be moved into a WG Day from the page.

WG bookings never send customer SMS, payment reminders, review requests, or
create Xero invoices automatically (wg_jobs.is_wg_order guards). Google
Calendar sync still runs.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import database as db
import double_booking
import wg_jobs
from booking_helpers import google_maps_url, tel_href
from booking_times import duration_hours_from_times, format_time_12h, normalize_time_input
from crew import active_crew_names, crew_from_storage, crew_storage_value

SOURCE = wg_jobs.WG_ORDER_SOURCE
DAY_NAME = "WG Outdoor Life"
NAME_PREFIX = "WG - "  # older per-customer WG bookings
PICKUP_LABEL = "WG Outdoor Life"
DELIVERY_LABEL = "WG delivery run — see stops"
PAYMENT_TYPES = ("COD", "Confirmed", "Invoice", "Other")  # suggestions; WG also writes e.g. "YASU"
PAYMENT_TYPE_MAX = 30
DEFAULT_STATUS = "Confirmed"
FORM_PREFIX = "stops"
CALLOUT_MINUTES = 30

STOP_KEYS = db.WG_STOP_FIELDS

_REX_RE = re.compile(r"\b(?:rex\s*)?(\d{3,6})\b", re.IGNORECASE)
_NAME_PREFIX_RE = re.compile(r"^\s*wg\b\s*(?:outdoor\s+life\b)?\s*[-–—:]?\s*", re.IGNORECASE)


# --- small helpers ----------------------------------------------------------------


def plain_customer(name: Any) -> str:
    """Customer name without the "WG - " prefix (older per-customer bookings)."""
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


stops_label = wg_jobs.stops_label


def _stop_sort_key(stop: Dict[str, Any]) -> Tuple[str, int]:
    return (normalize_time_input(stop.get("start_time")) or "99:99", int(stop.get("_order") or 0))


# --- day / stop dicts ---------------------------------------------------------------


def empty_stop() -> Dict[str, Any]:
    stop: Dict[str, Any] = {key: "" for key in STOP_KEYS}
    stop.update({"payment_amount": None, "payment_amount_raw": "", "payment_type": "Invoice"})
    return stop


def empty_day(move_date: str = "") -> Dict[str, Any]:
    return {
        "booking_id": None,
        "move_date": move_date,
        "crew": [],
        "truck": "",
        "day_start": "",
        "day_finish": "",
        "override": False,
        "force_duplicate": False,
        "stops": [empty_stop()],
        "errors": [],
        "field_warnings": {},
    }


def clean_stop(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise one stop (from the form, the import, or an older WG booking)."""
    stop = {
        "customer": " ".join(str(raw.get("customer") or "").split()),
        "products": normalize_products(raw.get("products")),
        "start_time": normalize_time_input(raw.get("start_time")),
        "finish_time": normalize_time_input(raw.get("finish_time")),
        "address": " ".join(str(raw.get("address") or "").split()),
        "phone": str(raw.get("phone") or "").strip(),
        "payment_amount": _float(raw.get("payment_amount")),
        "payment_amount_raw": str(raw.get("payment_amount") if raw.get("payment_amount") is not None else "").strip(),
        "payment_type": normalize_payment_type(raw.get("payment_type")),
        "reference": " ".join(str(raw.get("reference") or "").split()),
        "notes": str(raw.get("notes") or "").replace("\r\n", "\n").strip(),
        "field_warnings": dict(raw.get("field_warnings") or {}),
        "imported": bool(raw.get("imported")),
        "_order": raw.get("_order", 0),
    }
    return stop


def stop_is_blank(raw: Dict[str, Any]) -> bool:
    """A stop card the user never filled in (the payment type default doesn't count)."""
    keys = ("customer", "products", "start_time", "finish_time", "address", "phone", "payment_amount", "reference", "notes")
    return not any(str(raw.get(key) if raw.get(key) is not None else "").strip() for key in keys)


def clean_day(raw: Dict[str, Any]) -> Dict[str, Any]:
    crew = raw.get("crew") or []
    if isinstance(crew, str):
        crew = crew_from_storage(crew)
    allowed = active_crew_names()
    stops = []
    for index, raw_stop in enumerate(raw.get("stops") or []):
        if stop_is_blank(raw_stop):
            continue
        stop = clean_stop(dict(raw_stop, _order=index))
        stops.append(stop)
    stops.sort(key=_stop_sort_key)
    booking_id = raw.get("booking_id")
    return {
        "booking_id": int(booking_id) if str(booking_id or "").strip().isdigit() else None,
        "move_date": str(raw.get("move_date") or "").strip()[:10],
        "crew": [name for name in crew if name in allowed],
        "truck": str(raw.get("truck") or "").strip(),
        "day_start": normalize_time_input(raw.get("day_start")),
        "day_finish": normalize_time_input(raw.get("day_finish")),
        "override": bool(raw.get("override")),
        "force_duplicate": bool(raw.get("force_duplicate")),
        "stops": stops,
        "errors": [],
        "field_warnings": dict(raw.get("field_warnings") or {}),
    }


def parse_day_form(form: Any) -> Dict[str, Any]:
    """
    The WG Day form: date / crew (checkboxes "crew") / truck / day_start / day_finish
    once, then stops named "stops-<n>-<key>" in page order.
    """
    indexes = set()
    pattern = re.compile(r"^{0}-(\d+)-".format(FORM_PREFIX))
    for key in form.keys():
        match = pattern.match(key)
        if match:
            indexes.add(int(match.group(1)))
    stops = []
    for n in sorted(indexes):
        prefix = "{0}-{1}-".format(FORM_PREFIX, n)
        stops.append({key: form.get(prefix + key, "") for key in STOP_KEYS})
    crew = form.getlist("crew") if hasattr(form, "getlist") else form.get("crew") or []
    return {
        "booking_id": form.get("booking_id") or None,
        "move_date": form.get("move_date", ""),
        "crew": crew,
        "truck": form.get("truck", ""),
        "day_start": form.get("day_start", ""),
        "day_finish": form.get("day_finish", ""),
        "override": form.get("override") == "on",
        "force_duplicate": form.get("force_duplicate") == "on",
        "stops": stops,
    }


# --- times ---------------------------------------------------------------------------


def auto_times(stops: Iterable[Dict[str, Any]]) -> Tuple[str, str]:
    """First stop's start and the last finish (or start) of the day."""
    starts = [s["start_time"] for s in stops if s.get("start_time")]
    ends = [s.get("finish_time") or s.get("start_time") for s in stops if s.get("finish_time") or s.get("start_time")]
    return (min(starts) if starts else "", max(ends) if ends else "")


def day_times(day: Dict[str, Any]) -> Tuple[str, str]:
    """The WG Day booking's start / finish: typed day times win, else from the stops."""
    auto_start, auto_finish = auto_times(day["stops"])
    start = day.get("day_start") or auto_start
    finish = day.get("day_finish") or auto_finish
    if finish and start and finish <= start:
        finish = day.get("day_finish") or ""
    return start, finish


# --- validation ----------------------------------------------------------------------


def validate_stop(stop: Dict[str, Any]) -> List[str]:
    errors = []
    if not stop["customer"]:
        errors.append("Customer name is required.")
    if not stop["start_time"]:
        errors.append("Start time is required.")
    if stop["finish_time"] and stop["start_time"]:
        if duration_hours_from_times(stop["start_time"], stop["finish_time"]) is None:
            errors.append("Finish time must be after start time.")
    if not stop["address"]:
        errors.append("Address is required.")
    if stop["payment_amount_raw"] and stop["payment_amount"] is None:
        errors.append("Payment amount must be a number.")
    elif stop["payment_amount"] is not None and stop["payment_amount"] < 0:
        errors.append("Payment amount can't be negative.")
    if len(stop["payment_type"]) > PAYMENT_TYPE_MAX:
        errors.append("Payment type is too long (max {0} characters).".format(PAYMENT_TYPE_MAX))
    return errors


def validate_day(day: Dict[str, Any]) -> List[str]:
    """Day-level errors; each stop also gets stop["errors"]. Empty list = OK."""
    errors = []
    if not day["move_date"] or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day["move_date"]):
        errors.append("Date is required.")
    if not day["stops"]:
        errors.append("Add at least one stop.")
    for stop in day["stops"]:
        stop["errors"] = validate_stop(stop)
    bad = [str(i + 1) for i, s in enumerate(day["stops"]) if s["errors"]]
    if bad:
        errors.append("Stop {0}: fix the fields marked below.".format(", ".join(bad)))
    if day["day_start"] and day["day_finish"] and duration_hours_from_times(day["day_start"], day["day_finish"]) is None:
        errors.append("Day finish must be after day start.")
    if day["move_date"] and not errors:
        other = find_day(day["move_date"])
        if other is not None and int(other["id"]) != (day["booking_id"] or -1):
            errors.append(
                "There is already a WG Day on this date (booking #{0}). Open that day to add stops.".format(other["id"])
            )
    return errors


def _same_stop_reasons(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    reasons = []
    if _norm(a.get("customer")) and _norm(a.get("customer")) == _norm(b.get("customer")):
        reasons.append("same customer")
    if _norm(a.get("address")) and _norm(a.get("address")) == _norm(b.get("address")):
        reasons.append("same address")
    shared = reference_numbers(a.get("reference")) & reference_numbers(b.get("reference"))
    if shared:
        reasons.append("same REX {0}".format(", ".join(sorted(shared))))
    return reasons


def annotate_duplicates(day: Dict[str, Any]) -> int:
    """
    Mark stops that look like another stop of the day or an older WG booking on the
    same date (same customer, address or REX). Warning only. Returns how many.
    """
    older = [
        {
            "customer": plain_customer(row.get("customer_name")),
            "address": row.get("delivery_address") or "",
            "reference": row.get("wg_reference") or "",
            "label": "#{0} {1}".format(row["id"], row.get("customer_name") or ""),
            "booking_id": int(row["id"]),
        }
        for row in older_wg_bookings(day["move_date"]) if day.get("move_date")
    ]
    count = 0
    for index, stop in enumerate(day["stops"]):
        found = []
        for other_index in range(index):
            reasons = _same_stop_reasons(stop, day["stops"][other_index])
            if reasons:
                found.append({"label": "Stop {0} {1}".format(other_index + 1, day["stops"][other_index]["customer"]), "reasons": reasons})
        for row in older:
            reasons = _same_stop_reasons(stop, row)
            if reasons:
                found.append({"label": row["label"], "reasons": reasons, "booking_id": row["booking_id"]})
        stop["duplicates"] = found
        if found:
            count += 1
            msgs = stop["field_warnings"].setdefault("customer", [])
            if "Possible duplicate" not in msgs:
                msgs.append("Possible duplicate")
    return count


# --- the WG Day booking ----------------------------------------------------------------


def find_day(move_date: str) -> Optional[Dict[str, Any]]:
    """The WG Day booking on a date (lowest id if, unexpectedly, there are two)."""
    for row in db.list_by_date(move_date):
        b = dict(row)
        if wg_jobs.is_wg_day(b):
            return b
    return None


def older_wg_bookings(move_date: str) -> List[Dict[str, Any]]:
    """WG bookings on a date that are not a WG Day: per-customer orders and hand-made "WG" jobs."""
    out = []
    for row in db.list_by_date(move_date):
        b = dict(row)
        if wg_jobs.is_wg_outdoor_life(b) and not wg_jobs.is_wg_day(b):
            out.append(b)
    out.sort(key=lambda b: (normalize_time_input(b.get("start_time")) or "99:99", int(b["id"])))
    return out


def booking_data(day: Dict[str, Any]) -> Dict[str, Any]:
    """Booking fields for conflict checks / create / update."""
    crew = list(day.get("crew") or [])
    start, finish = day_times(day)
    duration = ""
    if start and finish:
        hours = duration_hours_from_times(start, finish)
        duration = "{0:g}".format(hours) if hours else ""
    return {
        "customer_name": DAY_NAME,
        "move_date": day["move_date"],
        "num_movers": max(1, len(crew)) if crew else 2,
        "start_time": start,
        "finish_time": finish,
        "duration_hours": duration,
        "crew": crew,
        "crew_csv": crew_storage_value(crew),
        "truck_assigned": day["truck"],
        "status": DEFAULT_STATUS,
    }


def conflict_check(day: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, Any]], bool]:
    """Existing double-booking rules (different trucks never conflict)."""
    return double_booking.validate_save(
        booking_data(day), booking_id=day.get("booking_id"), override_confirmed=day.get("override", False)
    )


def crew_warnings(day: Dict[str, Any]) -> List[str]:
    from resource_conflicts import find_resource_conflict_warnings

    data = booking_data(day)
    booking_id = day.get("booking_id")
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


def _new_day_rates(crew_count: int) -> Tuple[float, float]:
    """Hourly rate + 30 min call-out for a new WG Day, like a new booking with that many movers."""
    import callout_pricing
    import config
    import mover_pricing

    rate = mover_pricing.hourly_rate_for_movers(max(2, crew_count)) or config.INVOICE_DEFAULT_HOURLY_RATE
    return float(rate), callout_pricing.fee_amount(rate, CALLOUT_MINUTES)


def _after_save(booking_id: int) -> List[str]:
    """Google Calendar + profit. No SMS, payment link or Xero for WG Days."""
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


def _write_booking(day: Dict[str, Any]) -> int:
    data = booking_data(day)
    booking_id = day.get("booking_id")
    if booking_id is None:
        rate, callout_fee = _new_day_rates(len(data["crew"]))
        return db.create_booking(
            customer_name=DAY_NAME,
            phone="",
            email="",
            pickup_address=PICKUP_LABEL,
            delivery_address=DELIVERY_LABEL,
            move_date=data["move_date"],
            num_movers=data["num_movers"],
            notes="",
            start_time=data["start_time"],
            finish_time=data["finish_time"],
            duration_hours=data["duration_hours"],
            crew=data["crew_csv"],
            hourly_rate=rate,
            callout_fee=callout_fee,
            callout_minutes=CALLOUT_MINUTES,
            gst_enabled=1,
            payment_status="Unpaid",
            invoice_status="",
            status=data["status"],
        )
    row = dict(db.get_booking(booking_id))
    db.update_booking(
        booking_id=booking_id,
        customer_name=row.get("customer_name") or DAY_NAME,
        phone=row.get("phone") or "",
        email=row.get("email") or "",
        pickup_address=row.get("pickup_address") or PICKUP_LABEL,
        delivery_address=row.get("delivery_address") or DELIVERY_LABEL,
        move_date=data["move_date"],
        num_movers=data["num_movers"] if data["crew"] else int(row.get("num_movers") or 2),
        notes=row.get("notes") or "",
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
    return booking_id


def stored_stop(stop: Dict[str, Any]) -> Dict[str, Any]:
    return {key: stop.get(key) for key in STOP_KEYS}


def save_day(raw: Dict[str, Any]) -> Tuple[Optional[int], Dict[str, Any]]:
    """
    Create (or, with booking_id, update) the WG Day booking and replace its stops.
    Returns (booking_id or None, result) — result has day (cleaned, with per-stop
    errors / duplicates), errors, conflicts, warnings, messages. Nothing is written
    when there are errors, an unconfirmed duplicate or an unconfirmed time conflict.
    """
    day = clean_day(raw)
    result: Dict[str, Any] = {"day": day, "errors": validate_day(day), "conflicts": [], "warnings": [], "messages": []}
    if result["errors"]:
        return None, result
    if annotate_duplicates(day) and not day["force_duplicate"]:
        result["errors"].append('Possible duplicate stop — check the stops marked below, then tick "Save anyway".')
        return None, result
    conflict_errors, conflicts, _override = conflict_check(day)
    result["conflicts"] = conflicts
    if conflict_errors:
        result["errors"].extend(conflict_errors)
        return None, result
    result["warnings"] = crew_warnings(day)

    booking_id = _write_booking(day)
    db.replace_wg_stops(booking_id, [stored_stop(s) for s in day["stops"]])
    db.update_booking_integration_fields(booking_id, {"truck_assigned": day["truck"]})
    if conflicts and day.get("override"):
        from datetime import datetime

        db.update_booking_integration_fields(
            booking_id, {"double_booking_override_at": datetime.utcnow().isoformat(timespec="seconds")}
        )
    result["messages"] = _after_save(booking_id)
    day["booking_id"] = booking_id
    return booking_id, result


# --- reading back ----------------------------------------------------------------------


def stop_view(stop: Dict[str, Any], number: int) -> Dict[str, Any]:
    """A saved stop for the run sheet / day view (display strings + links)."""
    amount = stop.get("payment_amount")
    return {
        **{key: stop.get(key) for key in STOP_KEYS},
        "number": number,
        "time_range": time_range_label(stop.get("start_time"), stop.get("finish_time")),
        "payment_display": " · ".join(part for part in (format_amount(amount), stop.get("payment_type") or "") if part),
        "map_url": google_maps_url(stop.get("address")),
        "tel_href": tel_href(stop.get("phone")),
    }


def load_stops(booking_id: int) -> List[Dict[str, Any]]:
    stops = db.list_wg_stops(booking_id)
    for index, stop in enumerate(stops):
        stop["_order"] = index
    stops.sort(key=_stop_sort_key)
    return stops


def day_from_booking(booking: Any) -> Dict[str, Any]:
    """The edit form for a saved WG Day. Day times show only when they differ from the stops."""
    b = dict(booking)
    stops = []
    for stop in load_stops(int(b["id"])):
        amount = stop.get("payment_amount")
        stops.append(
            dict(
                clean_stop(stop),
                payment_amount=amount,
                payment_amount_raw="{0:.2f}".format(float(amount)).rstrip("0").rstrip(".") if amount not in (None, "") else "",
            )
        )
    start = normalize_time_input(b.get("start_time"))
    finish = normalize_time_input(b.get("finish_time"))
    auto_start, auto_finish = auto_times(stops)
    return {
        "booking_id": int(b["id"]),
        "move_date": str(b.get("move_date") or "")[:10],
        "crew": crew_from_storage(b.get("crew")),
        "truck": b.get("truck_assigned") or "",
        "day_start": start if start != auto_start else "",
        "day_finish": finish if finish != auto_finish else "",
        "override": False,
        "force_duplicate": False,
        "stops": stops or [empty_stop()],
        "errors": [],
        "field_warnings": {},
    }


def run_sheet(booking: Any) -> Dict[str, Any]:
    """Everything the Staff Portal run sheet and its PDF show, stops in time order."""
    from datetime import date as date_cls

    b = dict(booking)
    move_date = str(b.get("move_date") or "")[:10]
    try:
        heading = date_cls.fromisoformat(move_date).strftime("%A %-d %B %Y")
    except ValueError:
        heading = move_date
    stops = [stop_view(stop, index + 1) for index, stop in enumerate(load_stops(int(b["id"])))]
    return {
        "booking_id": int(b["id"]),
        "move_date": move_date,
        "date_heading": heading,
        "crew": crew_from_storage(b.get("crew")),
        "crew_display": " / ".join(crew_from_storage(b.get("crew"))) or "Unassigned",
        "truck": b.get("truck_assigned") or "No truck",
        "time_range": time_range_label(b.get("start_time"), b.get("finish_time")),
        "status": b.get("status") or "",
        "stops": stops,
        "stop_count": len(stops),
        "stops_label": stops_label(len(stops)),
    }


def staff_fields(booking: Any) -> Dict[str, Any]:
    """WG keys for Staff Portal / calendar / daily-jobs payloads (is_wg False for normal bookings)."""
    b = dict(booking) if booking is not None else {}
    if wg_jobs.is_wg_day(b):
        from datetime import date as date_cls

        count = int(b.get("wg_stop_count") or 0)
        booking_id = b.get("id")
        try:
            heading = date_cls.fromisoformat(str(b.get("move_date") or "")[:10]).strftime("%A %-d %B")
        except ValueError:
            heading = ""
        return {
            "is_wg": True,
            "is_wg_day": True,
            "wg_stop_count": count,
            "wg_stops_label": stops_label(count),
            "wg_day_heading": heading,
            "wg_truck": b.get("truck_assigned") or "No truck",
            "wg_run_sheet_url": "/staff/wg/{0}".format(booking_id) if booking_id else "",
            "wg_run_sheet_pdf_url": "/staff/wg/{0}/run-sheet.pdf".format(booking_id) if booking_id else "",
        }
    if not wg_jobs.is_wg_order(b):
        return {"is_wg": False, "is_wg_day": False}
    return {
        "is_wg": True,
        "is_wg_day": False,
        "wg_customer": plain_customer(b.get("customer_name")),
        "wg_products": b.get("wg_products") or "",
        "wg_reference": b.get("wg_reference") or "",
        "wg_payment_display": " · ".join(
            part for part in (format_amount(b.get("wg_payment_amount")), b.get("wg_payment_type") or "") if part
        ),
    }


def copy_staff_fields(job: Dict[str, Any]) -> Dict[str, Any]:
    """The staff_fields keys of an already-serialised job (calendar / weekly cards)."""
    out = {key: value for key, value in job.items() if key.startswith("wg_")}
    out["is_wg"] = bool(job.get("is_wg"))
    out["is_wg_day"] = bool(job.get("is_wg_day"))
    return out


# --- moving older WG bookings into a WG Day (owner action) --------------------------------


def adopt_booking_as_day(booking_id: int) -> Tuple[bool, str]:
    """
    Make a hand-made "WG" day booking the date's WG Day (no stops yet). Its crew,
    truck, times, invoice and payment status are kept exactly as they are.
    """
    row = db.get_booking(booking_id)
    if row is None:
        return False, "Booking not found."
    b = dict(row)
    if wg_jobs.is_wg_day(b):
        return True, "Booking #{0} is already the WG Day.".format(booking_id)
    if wg_jobs.is_wg_order(b):
        return False, "Booking #{0} is a single WG customer order — use “Move into WG Day” instead.".format(booking_id)
    if not wg_jobs.is_wg_outdoor_life(b):
        return False, "Booking #{0} is not a WG booking.".format(booking_id)
    move_date = str(b.get("move_date") or "")[:10]
    if find_day(move_date) is not None:
        return False, "There is already a WG Day on this date."
    db.replace_wg_stops(booking_id, [])
    return True, "Booking #{0} is now the WG Day for this date. Add its stops below.".format(booking_id)


def _order_can_move(b: Dict[str, Any]) -> Optional[str]:
    if str(b.get("payment_status") or "").strip().lower() == "paid":
        return "it is marked Paid"
    if str(b.get("xero_invoice_id") or "").strip():
        return "it has a Xero invoice"
    if str(b.get("invoice_sent_at") or "").strip():
        return "its invoice was sent"
    return None


def move_order_into_day(booking_id: int) -> Tuple[Optional[int], str]:
    """
    Turn an older per-customer WG booking ("WG - <customer>") into a stop of its
    date's WG Day (a new WG Day with the order's crew / truck if there is none), then
    delete the old booking. Refused for Paid, Xero-linked or sent-invoice bookings.
    """
    row = db.get_booking(booking_id)
    if row is None:
        return None, "Booking not found."
    b = dict(row)
    if not wg_jobs.is_wg_order(b) or wg_jobs.is_wg_day(b):
        return None, "Booking #{0} is not a single WG customer order.".format(booking_id)
    blocked = _order_can_move(b)
    if blocked:
        return None, "Booking #{0} was left as it is because {1}.".format(booking_id, blocked)
    move_date = str(b.get("move_date") or "")[:10]
    stop = clean_stop(
        {
            "customer": plain_customer(b.get("customer_name")),
            "products": b.get("wg_products"),
            "start_time": b.get("start_time"),
            "finish_time": b.get("finish_time"),
            "address": b.get("delivery_address"),
            "phone": b.get("phone"),
            "payment_amount": b.get("wg_payment_amount"),
            "payment_type": b.get("wg_payment_type"),
            "reference": b.get("wg_reference"),
            "notes": b.get("notes"),
        }
    )
    target = find_day(move_date)
    if target is None:
        day = clean_day(
            {
                "move_date": move_date,
                "crew": crew_from_storage(b.get("crew")),
                "truck": b.get("truck_assigned") or "",
                "stops": [stop],
            }
        )
        day_id = _write_booking(day)
        db.update_booking_integration_fields(day_id, {"truck_assigned": day["truck"]})
        stops = [stop]
    else:
        day_id = int(target["id"])
        stops = load_stops(day_id) + [dict(stop, _order=10_000)]
        stops.sort(key=_stop_sort_key)
    db.replace_wg_stops(day_id, [stored_stop(s) for s in stops])
    _delete_order_booking(booking_id)
    _after_save(day_id)
    return day_id, "Moved #{0} {1} into the WG Day (booking #{2}).".format(booking_id, stop["customer"], day_id)


def _delete_order_booking(booking_id: int) -> None:
    """Delete an older per-customer WG booking the same way Booking → Delete does."""
    import services

    row = db.get_booking(booking_id)
    if row is None:
        return
    try:
        services.after_booking_deleted(services.booking_to_dict(row))
    except Exception:  # noqa: BLE001 — never block the move on Google Calendar
        pass
    db.delete_booking(booking_id)
