"""Optional second pickup address (bookings.pickup_address_2).

Most jobs have one pickup. A few collect from two places before the final
delivery, so the booking can hold a second pickup address. Empty means a normal
single-pickup booking, and every screen/PDF then looks exactly as before.

Forms show the field behind "Add another pickup address"; FORM_FLAG is a hidden
input next to it. When a form has no FORM_FLAG (other forms that share the
booking parser), the saved Pickup 2 is left untouched.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from booking_helpers import apple_maps_url, pickup_suburb

FIELD = "pickup_address_2"
FORM_FLAG = "pickup_address_2_enabled"


def _get(booking: Any, key: str) -> str:
    if booking is None:
        return ""
    try:
        value = booking.get(key) if hasattr(booking, "get") else booking[key]
    except (KeyError, IndexError):
        value = None
    return str(value or "").strip()


def second_pickup(booking: Any) -> str:
    return _get(booking, FIELD)


def has_second_pickup(booking: Any) -> bool:
    return bool(second_pickup(booking))


def pickup_addresses(booking: Any) -> List[str]:
    """Non-empty pickups in order: [Pickup 1] or [Pickup 1, Pickup 2]."""
    return [a for a in (_get(booking, "pickup_address"), second_pickup(booking)) if a]


def parse_form(form: Any) -> Optional[str]:
    """
    Pickup 2 from a booking form. None = the form has no Pickup 2 section (keep
    what is saved). "" = section switched off or blank (clear it on save).
    """
    if FORM_FLAG not in form:
        return None
    if str(form.get(FORM_FLAG) or "").strip() != "1":
        return ""
    return str(form.get(FIELD) or "").strip()


def staff_fields(booking: Any) -> Dict[str, str]:
    """Pickup 2 keys for Staff Portal job payloads (empty strings when none)."""
    second = second_pickup(booking)
    return {
        "pickup_address_2": second,
        "pickup_2_label": (pickup_suburb(second) or second) if second else "",
        "pickup_2_map_url": apple_maps_url(second) if second else "",
    }


def pickup_text_lines(booking: Any, *, label: str = "Pickup") -> List[str]:
    """Plain-text lines for SMS / calendar / PDFs."""
    pickups = pickup_addresses(booking)
    if len(pickups) < 2:
        return ["{0}: {1}".format(label, pickups[0] if pickups else "—")]
    return ["{0} {1}: {2}".format(label, i, addr) for i, addr in enumerate(pickups, 1)]
