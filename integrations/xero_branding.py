"""Sync Xero branding themes and organisation header for invoice layout."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from integrations import company_config

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOGO = ROOT / "static" / "branding" / "japanese-removals-logo.png"
INVOICE_LOGO = ROOT / "static" / "branding" / "japanese-removals-invoice-logo.png"
INVOICE_LOGO_HIRES = ROOT / "static" / "branding" / "japanese-removals-invoice-logo-hires.png"
XERO_LOGO = ROOT / "static" / "branding" / "japanese-removals-logo-xero.png"
XERO_MAX_WIDTH = 400
XERO_MAX_HEIGHT = 120


def invoice_logo_path(*, hires: bool = False) -> Path:
    """Official wide invoice logo (Japanese Removals + region text)."""
    settings = company_config.get_settings()
    custom = (settings.get("logo_path") or "").strip()
    if custom:
        path = Path(custom)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            if hires and path.name == INVOICE_LOGO.name and INVOICE_LOGO_HIRES.is_file():
                return INVOICE_LOGO_HIRES
            return path
    if hires and INVOICE_LOGO_HIRES.is_file():
        return INVOICE_LOGO_HIRES
    if INVOICE_LOGO.is_file():
        return INVOICE_LOGO
    return DEFAULT_LOGO if DEFAULT_LOGO.is_file() else XERO_LOGO


def invoice_logo_url() -> str:
    return "/static/branding/japanese-removals-invoice-logo.png"


def logo_source_path() -> Path:
    settings = company_config.get_settings()
    custom = (settings.get("logo_path") or "").strip()
    if custom:
        path = Path(custom)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            return path
    return invoice_logo_path() if INVOICE_LOGO.is_file() else (
        DEFAULT_LOGO if DEFAULT_LOGO.is_file() else XERO_LOGO
    )


def prepare_xero_logo() -> Optional[Path]:
    """Resize the official logo to Xero's recommended invoice dimensions."""
    source = invoice_logo_path()
    if not source.is_file():
        source = logo_source_path()
    if not source.is_file():
        return None
    try:
        from PIL import Image
    except ImportError:
        return source

    image = Image.open(source).convert("RGBA")
    width, height = image.size
    scale = min(XERO_MAX_WIDTH / width, XERO_MAX_HEIGHT / height, 1.0)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    if new_size != (width, height):
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    XERO_LOGO.parent.mkdir(parents=True, exist_ok=True)
    image.save(XERO_LOGO, format="PNG", optimize=True)
    return XERO_LOGO


def invoice_header_lines() -> List[str]:
    """Company block for the Xero invoice header (above line items)."""
    settings = company_config.get_settings()
    lines: List[str] = []
    name = str(settings.get("company_name") or "").strip()
    phone = str(settings.get("company_phone") or "").strip()
    email = str(settings.get("company_email") or "").strip()
    if name:
        lines.append(name)
    if phone:
        lines.append("Phone: {0}".format(phone))
    if email:
        lines.append("Email: {0}".format(email))
    lines.extend(invoice_payment_advice_lines())
    return lines


def invoice_payment_advice_lines(booking: Optional[Dict[str, Any]] = None) -> List[str]:
    """Bank details block."""
    s = company_config.get_settings()
    booking = booking or {}
    account_name = (booking.get("invoice_bank_account_name") or "").strip() or s["bank_account_name"]
    bsb = (booking.get("invoice_bank_bsb") or "").strip() or s["bank_bsb"]
    account = (booking.get("invoice_bank_account") or "").strip() or s["bank_account_number"]
    return [
        "Bank Details",
        str(account_name),
        "BSB: {0}".format(bsb),
        "Account: {0}".format(account),
    ]


def invoice_payment_advice_text(booking: Optional[Dict[str, Any]] = None) -> str:
    return "\n".join(invoice_payment_advice_lines(booking))


def organisation_address_payload(booking: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Organisation address lines shown above the line-item table on Xero invoices.

    Xero allows four address lines — company name, phone, email, and bank details.
    Pickup/delivery addresses are intentionally omitted from customer invoices.
    """
    settings = company_config.get_settings()
    booking = booking or {}
    name = str(settings.get("company_name") or "").strip()
    phone = str(settings.get("company_phone") or "").strip()
    email = str(settings.get("company_email") or "").strip()
    account_name = (booking.get("invoice_bank_account_name") or "").strip() or settings["bank_account_name"]
    bsb = (booking.get("invoice_bank_bsb") or "").strip() or settings["bank_bsb"]
    account = (booking.get("invoice_bank_account") or "").strip() or settings["bank_account_number"]
    location = str(settings.get("company_location") or "").strip()
    bank_line = "Bank: {0} · BSB: {1} · Account: {2}".format(account_name, bsb, account)
    return {
        "AddressType": "POBOX",
        "AddressLine1": name,
        "AddressLine2": "Phone: {0}".format(phone) if phone else "",
        "AddressLine3": "Email: {0}".format(email) if email else "",
        "AddressLine4": bank_line,
        "City": location,
        "Region": "Western Australia",
        "PostalCode": "",
        "Country": "Australia",
    }


def organisation_payload(booking: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    settings = company_config.get_settings()
    payload: Dict[str, Any] = {}
    name = str(settings.get("company_name") or "").strip()
    if name:
        payload["Name"] = name
    legal = str(settings.get("company_legal_name") or "").strip()
    if legal:
        payload["LegalName"] = legal

    header = organisation_address_payload(booking)
    # Invoices display the organisation postal/street address — update both types.
    payload["Addresses"] = [
        dict(header, AddressType="POBOX"),
        dict(header, AddressType="STREET"),
    ]

    phone = str(settings.get("company_phone") or "").strip()
    if phone:
        payload["Phones"] = [
            {"PhoneType": "DEFAULT", "PhoneNumber": phone, "PhoneAreaCode": "", "PhoneCountryCode": ""}
        ]

    return payload


def list_branding_themes(api_request) -> List[Dict[str, Any]]:
    result = api_request("GET", "BrandingThemes")
    return list(result.get("BrandingThemes") or [])


def resolve_branding_theme_id(api_request=None) -> str:
    settings = company_config.get_settings()
    configured = (settings.get("xero_branding_theme_id") or "").strip()
    if configured:
        return configured
    if not api_request:
        return ""
    try:
        themes = list_branding_themes(api_request)
    except Exception:
        return ""
    if not themes:
        return ""
    themes = sorted(themes, key=lambda item: item.get("SortOrder", 999))
    return str(themes[0].get("BrandingThemeID") or "")


def branding_status(api_request) -> Dict[str, Any]:
    theme_id = resolve_branding_theme_id(api_request)
    themes = list_branding_themes(api_request)
    theme = next((item for item in themes if item.get("BrandingThemeID") == theme_id), None)
    logo_path = prepare_xero_logo() or logo_source_path()
    return {
        "theme_id": theme_id,
        "theme_name": (theme or {}).get("Name") or "",
        "logo_url": (theme or {}).get("LogoUrl") or "",
        "logo_ready": bool((theme or {}).get("LogoUrl")),
        "logo_file": str(logo_path) if logo_path and logo_path.is_file() else "",
        "themes": [
            {
                "id": item.get("BrandingThemeID", ""),
                "name": item.get("Name", ""),
                "logo_url": item.get("LogoUrl", ""),
                "sort_order": item.get("SortOrder", 0),
            }
            for item in themes
        ],
    }


def sync_branding_theme(api_request, booking: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """
    Push invoice header contact info and bank details into Xero branding/org settings.

    Logo must be uploaded once in Xero (Settings → Invoice settings → Branding themes).
    """
    if not api_request:
        return False, "Xero API unavailable."

    settings = company_config.get_settings()
    theme_id = resolve_branding_theme_id(api_request)
    if not theme_id:
        return (
            False,
            "No Xero branding theme ID found. Reconnect Xero (Settings scope required), "
            "then open Settings → Company defaults and choose a branding theme.",
        )

    company_config.save_settings({"xero_branding_theme_id": theme_id})
    messages: List[str] = []

    payment_advice = invoice_payment_advice_text(booking)
    try:
        api_request(
            "POST",
            "BrandingThemes/{0}".format(theme_id),
            {
                "BrandingThemes": [
                    {
                        "BrandingThemeID": theme_id,
                        "PaymentAdvice": payment_advice,
                    }
                ]
            },
        )
        messages.append("Bank details synced to branding theme payment advice.")
    except Exception as exc:
        detail = str(exc)
        if "403" in detail or "Forbidden" in detail:
            messages.append(
                "Payment advice not updated via API (403). Set once in Xero → Settings → "
                "Invoice settings → Branding themes → Edit → Payment advice."
            )
        elif "401" in detail or "Unauthorized" in detail:
            return (
                False,
                "Could not update branding theme — Xero token missing Settings scope. "
                "Open Settings → Xero, click Connect Xero again, then retry Sync invoice branding.",
            )
        else:
            messages.append("Payment advice sync failed: {0}".format(exc))

    if settings.get("xero_sync_org_header", True):
        org_payload = organisation_payload(booking)
        if org_payload:
            try:
                api_request("POST", "Organisation", {"Organisations": [org_payload]})
                messages.append(
                    "Invoice header synced to Xero organisation (company name, phone, email, bank details)."
                )
            except Exception as exc:
                detail = str(exc)
                if "404" in detail or "Not Found" in detail:
                    messages.append(
                        "Organisation header cannot be updated via API (read-only). Set once in "
                        "Xero → Settings → Organisation details → Postal address: "
                        "Line 1 Japanese Removals, Line 2 Phone: 0481 089 573, "
                        "Line 3 Email: info@japaneseremovals.com.au, "
                        "Line 4 Bank: JR West Pty Ltd · BSB: 036308 · Account: 405623. "
                        "Remove the old street address (20b Tribute St)."
                    )
                elif "401" in detail or "Unauthorized" in detail:
                    messages.append(
                        "Organisation header not synced — reconnect Xero with Settings scope "
                        "(Settings → Xero → Connect Xero), then sync again."
                    )
                else:
                    messages.append(
                        "Organisation header was not synced ({0}). Set details manually in "
                        "Xero → Settings → Organisation details.".format(exc)
                    )

    logo_path = prepare_xero_logo()
    status = branding_status(api_request)
    if not status.get("logo_ready"):
        if logo_path and logo_path.is_file():
            messages.append(
                "Upload the logo to Xero once: Settings → Invoice settings → Branding themes → "
                "Upload logo (use file: {0}).".format(logo_path)
            )
        else:
            messages.append("Logo file missing — add static/branding/japanese-removals-logo.png.")

    return True, " ".join(messages)


def branding_theme_id_for_invoice() -> str:
    """Configured theme ID only — avoids live API calls while building payloads."""
    return (company_config.get_settings().get("xero_branding_theme_id") or "").strip()
