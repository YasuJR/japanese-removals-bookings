"""SMS automation settings — templates and per-trigger toggles."""

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import config

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "sms_settings.json"

TEMPLATE_KEYS = (
    "booking_confirmation",
    "booking_confirmed",
    "booking_reminder",
    "thank_you",
    "payment_reminder",
    "payment_confirmation",
    "eta_on_route",
    "unpaid_invoice_reminder",
)

MANUAL_TEMPLATE_KEYS = (
    "booking_confirmation",
    "payment_reminder",
    "thank_you",
)

TEMPLATE_LABELS = {
    "booking_confirmation": "Booking confirmation (manual send)",
    "booking_confirmed": "Booking confirmed (Pending → Confirmed)",
    "booking_reminder": "Booking reminder (1 day before move)",
    "thank_you": "Thank you message (status → Completed)",
    "payment_reminder": "Payment reminder (invoice overdue)",
    "payment_confirmation": "Payment confirmation (after Stripe payment)",
    "eta_on_route": "ETA on route (driver travelling to customer)",
    "unpaid_invoice_reminder": "Unpaid invoice reminder (3, 7, 14 days)",
}

PLACEHOLDER_HELP = (
    "{customer_name}, {first_name}, {move_date}, {start_time}, {driver_name}, "
    "{eta_minutes}, {pickup}, {delivery}, {ref}, {due_date}, {amount}, "
    "{amount_due}, {invoice_number}, {invoice_link}, {company_name}, {company_phone}"
)

DEFAULT_TEMPLATES = {
    "booking_confirmation": (
        "{company_name}: Hi {customer_name}, your move is confirmed for "
        "{move_date}. Ref #{ref}. Pickup: {pickup}. Delivery: {delivery}. "
        "Questions? Call {company_phone}."
    ),
    "booking_confirmed": (
        "Hi {first_name},\n"
        "Thank you for booking with Japanese Removals.\n"
        "Your move has been confirmed for {move_date} at {start_time}.\n"
        "We will contact you closer to the move date.\n"
        "Yasu\n"
        "Japanese Removals"
    ),
    "booking_reminder": (
        "{company_name}: Hi {customer_name}, reminder — your move is tomorrow "
        "({move_date}). Ref #{ref}. Pickup: {pickup}. Delivery: {delivery}. "
        "Questions? Call {company_phone}."
    ),
    "thank_you": (
        "{company_name}: Thank you {customer_name}! Your move (Ref #{ref}) is "
        "complete. We appreciate your business. Questions? Call {company_phone}."
    ),
    "payment_reminder": (
        "Hi {first_name}, this is a friendly reminder that your Japanese Removals "
        "invoice is still unpaid. You can pay by bank transfer or card from your "
        "invoice link: {invoice_link} Thank you, Yasu"
    ),
    "payment_confirmation": (
        "Hi {first_name}, thank you for your payment. Your Japanese Removals "
        "booking is confirmed as paid. See you soon. Yasu"
    ),
    "eta_on_route": (
        "Hi {first_name},\n"
        "My name is {driver_name} from Japanese Removals.\n"
        "We are approximately {eta_minutes} minutes away.\n"
        "See you soon.\n"
        "Yasu\n"
        "Japanese Removals"
    ),
    "unpaid_invoice_reminder": (
        "Hi {first_name},\n"
        "Just a friendly reminder that payment for your Japanese Removals invoice "
        "{invoice_number} is still outstanding.\n"
        "Amount: {amount_due}\n"
        "You can pay by bank transfer or credit card using the invoice link.\n"
        "Thank you,\n"
        "Yasu\n"
        "Japanese Removals"
    ),
}


def _default_settings() -> Dict[str, Any]:
    return {
        "automation_enabled": False,
        "triggers": {
            "booking_confirmation": {"enabled": False},
            "booking_confirmed": {"enabled": True},
            "booking_reminder": {"enabled": True},
            "thank_you": {"enabled": True},
            "payment_reminder": {"enabled": True},
            "payment_confirmation": {"enabled": True},
            "eta_on_route": {"enabled": True},
            "unpaid_invoice_reminder": {"enabled": True},
        },
        "templates": dict(DEFAULT_TEMPLATES),
    }


def _read_file() -> Dict[str, Any]:
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_file(data: Dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except OSError:
        pass


def _merged() -> Dict[str, Any]:
    defaults = _default_settings()
    stored = _read_file()
    triggers = dict(defaults["triggers"])
    triggers.update(stored.get("triggers") or {})
    templates = dict(defaults["templates"])
    templates.update(stored.get("templates") or {})
    return {
        "automation_enabled": stored.get(
            "automation_enabled", defaults["automation_enabled"]
        ),
        "triggers": triggers,
        "templates": templates,
    }


def is_automation_enabled() -> bool:
    if not config.SMS_ENABLED:
        return False
    return bool(_merged().get("automation_enabled", True))


def is_trigger_enabled(template_key: str) -> bool:
    if not is_automation_enabled():
        return False
    triggers = _merged().get("triggers") or {}
    entry = triggers.get(template_key) or {}
    return bool(entry.get("enabled", True))


def get_template(template_key: str) -> str:
    templates = _merged().get("templates") or {}
    return (
        templates.get(template_key)
        or DEFAULT_TEMPLATES.get(template_key)
        or ""
    ).strip()


def save_settings(
    automation_enabled: bool,
    triggers: Dict[str, bool],
    templates: Dict[str, str],
) -> None:
    data = _merged()
    data["automation_enabled"] = automation_enabled
    for key in TEMPLATE_KEYS:
        data["triggers"][key] = {"enabled": bool(triggers.get(key, True))}
        body = (templates.get(key) or "").strip()
        if body:
            data["templates"][key] = body
    _write_file(data)


def settings_for_form() -> Dict[str, Any]:
    merged = _merged()
    return {
        "automation_enabled": merged["automation_enabled"],
        "triggers": {
            key: bool((merged["triggers"].get(key) or {}).get("enabled", True))
            for key in TEMPLATE_KEYS
        },
        "templates": {
            key: get_template(key) for key in TEMPLATE_KEYS
        },
        "template_labels": TEMPLATE_LABELS,
        "placeholder_help": PLACEHOLDER_HELP,
        "template_keys": list(TEMPLATE_KEYS),
    }


def template_choices() -> List[Dict[str, str]]:
    return [
        {"key": key, "label": TEMPLATE_LABELS[key]}
        for key in TEMPLATE_KEYS
    ]
