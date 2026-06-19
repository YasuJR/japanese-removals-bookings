"""Maps links, click-to-call, and calendar title helpers."""

import html
import re
from typing import Any, Dict, List
from urllib.parse import quote

import config


def apple_maps_url(address: Any) -> str:
    """Apple Maps search URL for an address, or empty string."""
    text = str(address or "").strip()
    if not text:
        return ""
    return "https://maps.apple.com/?q={0}".format(quote(text))


def _phone_digits(phone: Any) -> str:
    """Digits only (and leading +), spaces stripped."""
    text = str(phone or "").strip()
    if not text:
        return ""
    return "".join(ch for ch in text if ch.isdigit() or ch == "+")


def _clean_phone_for_tel(phone: Any) -> str:
    """E.164 for tel: links (Call Customer)."""
    dial = _phone_digits(phone)
    if not dial:
        return ""
    if dial.startswith("0") and len(dial) == 10:
        return "+61{0}".format(dial[1:])
    if dial.startswith("61") and len(dial) >= 11:
        return "+{0}".format(dial)
    if dial.startswith("+"):
        return dial
    return dial


def _clean_phone_for_sms(phone: Any) -> str:
    """
    Digits-only local number for sms: (iPhone Messages).
    Example: 0432 393 117 → 0432393117
    """
    dial = _phone_digits(phone)
    if not dial:
        return ""
    if dial.startswith("+61") and len(dial) >= 12:
        return "0{0}".format(dial[3:])
    if dial.startswith("61") and len(dial) >= 11:
        return "0{0}".format(dial[2:])
    return dial


def tel_href(phone: Any) -> str:
    dial = _clean_phone_for_tel(phone)
    return "tel:{0}".format(dial) if dial else ""


def sms_href(phone: Any) -> str:
    """sms:{phone_number} for Messages app."""
    number = _clean_phone_for_sms(phone)
    return "sms:{0}".format(number) if number else ""


def customer_email_for_calendar(email: Any) -> str:
    """Customer email only — excludes calendar owner / organizer addresses."""
    text = str(email or "").strip()
    if not text:
        return ""
    if text.lower() in config.CALENDAR_ORGANIZER_EMAILS:
        return ""
    return text


def mailto_href(email: Any) -> str:
    text = customer_email_for_calendar(email)
    return "mailto:{0}".format(text) if text else ""


def calendar_event_summary(customer_name: str, **_kwargs: Any) -> str:
    """Google Calendar event title — Japanese Removals - [Customer Name]."""
    name = str(customer_name or "").strip() or "Customer"
    return "Japanese Removals - {0}".format(name)


def _title_case_words(text: str) -> str:
    return " ".join(word.capitalize() for word in text.split())


def pickup_suburb(address: Any) -> str:
    """Suburb from a comma-separated WA address, for schedule lists."""
    text = str(address or "").strip()
    if not text:
        return "—"
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) >= 2:
        candidate = parts[1]
        upper = candidate.upper()
        if re.match(r"^\d{4}$", candidate) or upper in ("WA", "W.A."):
            return _title_case_words(parts[0])
        return _title_case_words(candidate)
    return _title_case_words(parts[0]) if parts else "—"


def capitalize_suburb_in_address(address: Any) -> str:
    text = str(address or "").strip()
    if not text:
        return text

    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) <= 1:
        return _title_case_words(text)

    formatted = [parts[0]]
    for part in parts[1:]:
        upper = part.upper()
        if re.match(r"^WA(\s+\d{4})?$", upper):
            formatted.append(part.upper().replace("western australia", "WA"))
        elif re.match(r"^\d{4}$", part):
            formatted.append(part)
        elif upper in ("WA", "W.A."):
            formatted.append("WA")
        else:
            formatted.append(_title_case_words(part))
    return ", ".join(formatted)


def _calendar_href_uri(uri: str) -> str:
    """
    href value for calendar HTML — never escape tel:/sms:/mailto:/https:.
    """
    if not uri:
        return ""
    cleaned = uri.strip().replace('"', "").replace("<", "").replace(">", "")
    lowered = cleaned.lower()
    if lowered.startswith(
        ("tel:", "sms:", "sms://", "mailto:", "https://", "http://")
    ):
        return cleaned
    return html.escape(cleaned, quote=True)


def _html_calendar_link(href: str, label: str) -> str:
    """
    Minimal anchor for Google Calendar / Apple Calendar / Safari.
    Example: <a href="sms:0432393117">💬 Text Customer</a>
    """
    safe_href = _calendar_href_uri(href)
    if not safe_href:
        return ""
    return '<a href="{0}">{1}</a>'.format(safe_href, label)


def _html_block(content: str) -> str:
    return "<p>{0}</p>".format(content)


def _html_address_block(label: str, address_raw: str, display_text: str) -> str:
    """Pickup/Delivery — address text is the Apple Maps link."""
    maps_url = apple_maps_url(address_raw)
    if not maps_url or not display_text:
        return ""
    linked = _html_calendar_link(maps_url, html.escape(display_text))
    return _html_block("{0}<br>{1}".format(label, linked))


def _html_contact_section(phone_raw: str, email_raw: str) -> str:
    """Call / Text / Email — one minimal link per line, no Messages fallback."""
    links: List[str] = []
    call_uri = tel_href(phone_raw)
    if call_uri:
        links.append(_html_calendar_link(call_uri, "📞 Call Customer"))
    sms_uri = sms_href(phone_raw)
    if sms_uri:
        links.append(_html_calendar_link(sms_uri, "💬 Text Customer"))
    mail_uri = mailto_href(email_raw)
    if mail_uri:
        links.append(_html_calendar_link(mail_uri, "✉️ Email Customer"))
    if not links:
        return ""
    return _html_block("<br>".join(links))


def _notes_has_content(notes: Any) -> bool:
    text = str(notes or "").strip()
    if not text:
        return False
    if text in ("—", "-", "–"):
        return False
    if text.lower() in ("not provided", "n/a", "na", "none"):
        return False
    return True


def _strip_organizer_email_from_description(description: str) -> str:
    """Remove accidental plain-text organizer emails only (not HTML tags)."""
    result = description
    for blocked in config.CALENDAR_ORGANIZER_EMAILS:
        if not blocked:
            continue
        if blocked in result:
            result = re.sub(
                re.escape(blocked),
                "",
                result,
                flags=re.IGNORECASE,
            )
    return result


def _calendar_extra_charges_lines(booking: Dict[str, Any]) -> List[str]:
    from extra_charges import charge_line_total
    import invoice as invoice_module

    lines: List[str] = []
    resolved = invoice_module.resolve_booking_invoice(booking)
    for item in resolved.get("extra_charges") or []:
        desc = str(item.get("description") or "").strip()
        if not desc:
            continue
        qty = item.get("quantity") or 0
        unit = float(item.get("unit_price") or 0)
        line_total = charge_line_total(item)
        lines.append(
            "{0}: {1} × {2} = {3}".format(
                desc,
                qty,
                invoice_module.format_aud(unit),
                invoice_module.format_aud(line_total),
            )
        )
    return lines


def build_calendar_description(
    booking: Dict[str, Any],
    *,
    display_crew,
) -> str:
    """
    Google Calendar description with booking + invoice details.
    Includes customer, contact, addresses, crew, rates, charges, and total.
    """
    import invoice as invoice_module

    phone_raw = str(booking.get("phone") or "").strip()
    email_raw = customer_email_for_calendar(booking.get("email"))
    customer = str(booking.get("customer_name") or "").strip() or "—"
    crew_text = display_crew(booking)
    pickup_raw = str(booking.get("pickup_address") or "").strip()
    delivery_raw = str(booking.get("delivery_address") or "").strip()
    notes_text = str(booking.get("notes") or "").strip()
    booking_id = booking.get("id")

    resolved = invoice_module.resolve_booking_invoice(booking)
    totals = invoice_module.calculate_invoice_totals(resolved)
    hourly_rate = invoice_module.format_aud(totals["hourly_rate"])
    callout_fee = invoice_module.format_aud(totals["callout_fee"])
    total_incl = invoice_module.format_aud(totals["total"])
    total_label = (
        "Total (incl. GST): {0}".format(total_incl)
        if totals["gst_enabled"]
        else "Total: {0}".format(total_incl)
    )

    detail_lines = [
        "Customer: {0}".format(customer),
        "Phone: {0}".format(phone_raw or "—"),
        "Email: {0}".format(email_raw or "—"),
        "Pickup: {0}".format(pickup_raw or "—"),
        "Delivery: {0}".format(delivery_raw or "—"),
        "Notes: {0}".format(notes_text if _notes_has_content(notes_text) else "—"),
        "Crew: {0}".format(crew_text if crew_text and crew_text != "—" else "—"),
        "Hourly rate: {0}/hr".format(hourly_rate),
        "Callout fee: {0}".format(callout_fee),
    ]
    extra_lines = _calendar_extra_charges_lines(booking)
    if extra_lines:
        detail_lines.append("Extra charges:")
        detail_lines.extend(["  {0}".format(line) for line in extra_lines])
    else:
        detail_lines.append("Extra charges: —")
    detail_lines.append(total_label)
    if booking_id is not None and str(booking_id).strip():
        detail_lines.append("Booking #{0}".format(booking_id))

    blocks: List[str] = [
        _html_block("<br>".join(html.escape(line) for line in detail_lines))
    ]

    pickup_display = capitalize_suburb_in_address(pickup_raw) if pickup_raw else ""
    delivery_display = (
        capitalize_suburb_in_address(delivery_raw) if delivery_raw else ""
    )
    if pickup_raw and pickup_display:
        block = _html_address_block("📍 Pickup map:", pickup_raw, pickup_display)
        if block:
            blocks.append(block)
    if delivery_raw and delivery_display:
        block = _html_address_block("📍 Delivery map:", delivery_raw, delivery_display)
        if block:
            blocks.append(block)

    contact = _html_contact_section(phone_raw, email_raw)
    if contact:
        blocks.append(contact)

    description = "\n".join(blocks)
    return _strip_organizer_email_from_description(description).strip()
