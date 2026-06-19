"""Company defaults and invoice settings (admin-editable JSON)."""

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import config

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "company_settings.json"

GST_MODE_INCLUSIVE = "inclusive"
GST_MODE_EXCLUSIVE = "exclusive"


def _default_settings() -> Dict[str, Any]:
    return {
        "default_phone": "0481 089 573",
        "default_email": "info@japaneseremovals.com.au",
        "default_hourly_rate": 180.0,
        "default_callout_fee": 90.0,
        "default_gst_enabled": True,
        "gst_pricing_mode": GST_MODE_INCLUSIVE,
        "default_crew": ["Yasu", "Tom", "Ken"],
        "company_name": "Japanese Removals",
        "company_legal_name": "JR West Pty Ltd",
        "company_phone": "0481 089 573",
        "company_email": "info@japaneseremovals.com.au",
        "company_location": "Perth, Western Australia",
        "company_website": "www.japaneseremovals.com.au",
        "company_abn": "",
        "logo_path": "static/branding/japanese-removals-invoice-logo.png",
        "xero_branding_theme_id": "",
        "xero_sync_org_header": True,
        "bank_name": "Westpac",
        "bank_account_name": "JR West Pty Ltd",
        "bank_bsb": "036308",
        "bank_account_number": "405623",
    }


def _read_file() -> Dict[str, Any]:
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_file(data: Dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except OSError:
        pass


def get_settings() -> Dict[str, Any]:
    merged = _default_settings()
    stored = _read_file()
    merged.update(stored)
    if not isinstance(merged.get("default_crew"), list):
        merged["default_crew"] = _default_settings()["default_crew"]
    return merged


def save_settings(data: Dict[str, Any]) -> None:
    current = get_settings()
    current.update(data)
    _write_file(current)


def crew_options() -> List[str]:
    settings = get_settings()
    crew = settings.get("default_crew") or _default_settings()["default_crew"]
    return [str(name).strip() for name in crew if str(name).strip()]


def booking_form_defaults() -> Dict[str, Any]:
    s = get_settings()
    inv = default_invoice_fields()
    return {
        "phone": s["default_phone"],
        "email": s["default_email"],
        "hourly_rate": inv["hourly_rate"],
        "callout_fee": inv["callout_fee"],
        "gst_enabled": inv["gst_enabled"],
        "crew": list(s["default_crew"]),
    }


def default_invoice_fields() -> Dict[str, Any]:
    s = get_settings()
    return {
        "hourly_rate": float(s.get("default_hourly_rate") or 180),
        "callout_fee": float(s.get("default_callout_fee") or 90),
        "gst_enabled": 1 if s.get("default_gst_enabled", True) else 0,
        "payment_status": "Unpaid",
        "invoice_status": "",
    }


def gst_pricing_inclusive() -> bool:
    return get_settings().get("gst_pricing_mode", GST_MODE_INCLUSIVE) != GST_MODE_EXCLUSIVE


def invoice_business_lines() -> List[str]:
    s = get_settings()
    lines = []
    name = str(s.get("company_name") or "").strip()
    phone = str(s.get("company_phone") or "").strip()
    email = str(s.get("company_email") or "").strip()
    if name:
        lines.append(name)
    if phone:
        lines.append("Phone: {0}".format(phone))
    if email:
        lines.append("Email: {0}".format(email))
    return lines


def invoice_bank_lines(booking: Dict[str, Any] = None) -> List[str]:
    s = get_settings()
    booking = booking or {}
    account_name = (booking.get("invoice_bank_account_name") or "").strip() or s["bank_account_name"]
    bsb = (booking.get("invoice_bank_bsb") or "").strip() or s["bank_bsb"]
    account = (booking.get("invoice_bank_account") or "").strip() or s["bank_account_number"]
    return [
        "Bank Details:",
        str(account_name),
        "BSB: {0}".format(bsb),
        "Account: {0}".format(account),
    ]


def settings_for_form() -> Dict[str, Any]:
    s = get_settings()
    return {
        **s,
        "default_gst_enabled": bool(s.get("default_gst_enabled", True)),
        "gst_pricing_mode": s.get("gst_pricing_mode", GST_MODE_INCLUSIVE),
        "xero_sync_org_header": bool(s.get("xero_sync_org_header", True)),
        "default_crew_csv": ", ".join(crew_options()),
        "settings_path": str(SETTINGS_PATH.resolve()),
        "logo_path": s.get("logo_path") or _default_settings()["logo_path"],
    }
