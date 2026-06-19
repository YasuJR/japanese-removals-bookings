"""Stripe payment settings — API keys and card surcharge (UI-editable JSON)."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import config

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "stripe_settings.json"
DEFAULT_SURCHARGE_PERCENT = 2.0


def _field_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


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


def _merged() -> Dict[str, Any]:
    stored = _read_file()
    return {
        "stripe_enabled": bool(stored.get("stripe_enabled", False)),
        "publishable_key": _field_str(stored.get("publishable_key")),
        "secret_key": _field_str(stored.get("secret_key")),
        "webhook_secret": _field_str(stored.get("webhook_secret")),
        "card_surcharge_percent": float(
            stored.get("card_surcharge_percent", DEFAULT_SURCHARGE_PERCENT)
            or DEFAULT_SURCHARGE_PERCENT
        ),
        "xero_payment_account_code": _field_str(
            stored.get("xero_payment_account_code")
        ),
    }


def get_publishable_key() -> str:
    return _merged()["publishable_key"] or config.STRIPE_PUBLISHABLE_KEY


def get_secret_key() -> str:
    return _merged()["secret_key"] or config.STRIPE_SECRET_KEY


def get_webhook_secret() -> str:
    return _merged()["webhook_secret"] or config.STRIPE_WEBHOOK_SECRET


def is_enabled() -> bool:
    if not (_merged()["stripe_enabled"] or config.STRIPE_ENABLED):
        return False
    return bool(get_publishable_key()) and bool(get_secret_key())


def has_stored_secret() -> bool:
    return bool(_field_str(_read_file().get("secret_key"))) or bool(
        config.STRIPE_SECRET_KEY
    )


def has_stored_webhook_secret() -> bool:
    return bool(_field_str(_read_file().get("webhook_secret"))) or bool(
        config.STRIPE_WEBHOOK_SECRET
    )


def has_credentials() -> bool:
    return bool(get_publishable_key()) and bool(get_secret_key())


def is_ready() -> bool:
    return is_enabled() and has_credentials()


def surcharge_percent() -> float:
    value = _merged()["card_surcharge_percent"]
    try:
        pct = float(value)
    except (TypeError, ValueError):
        pct = DEFAULT_SURCHARGE_PERCENT
    return max(pct, 0.0)


def xero_payment_account_configured() -> bool:
    return bool(_merged()["xero_payment_account_code"])


def xero_payment_account_code() -> str:
    return _merged()["xero_payment_account_code"]


def save_settings(
    *,
    stripe_enabled: bool,
    publishable_key: str,
    secret_key: str = "",
    webhook_secret: str = "",
    card_surcharge_percent: float = DEFAULT_SURCHARGE_PERCENT,
    xero_payment_account_code: str = "",
) -> Dict[str, bool]:
    existing = _read_file()
    data = dict(existing)
    data["publishable_key"] = (publishable_key or "").strip()
    data["card_surcharge_percent"] = round(float(card_surcharge_percent or 0), 2)
    data["xero_payment_account_code"] = (xero_payment_account_code or "").strip()

    secret_updated = False
    webhook_updated = False
    new_secret = (secret_key or "").strip()
    existing_secret = _field_str(existing.get("secret_key"))
    if new_secret:
        data["secret_key"] = new_secret
        secret_updated = True
    elif existing_secret:
        data["secret_key"] = existing_secret

    new_webhook = (webhook_secret or "").strip()
    existing_webhook = _field_str(existing.get("webhook_secret"))
    if new_webhook:
        data["webhook_secret"] = new_webhook
        webhook_updated = True
    elif existing_webhook:
        data["webhook_secret"] = existing_webhook

    has_secret = bool(_field_str(data.get("secret_key"))) or bool(config.STRIPE_SECRET_KEY)
    has_pub = bool(data["publishable_key"]) or bool(config.STRIPE_PUBLISHABLE_KEY)
    data["stripe_enabled"] = bool(stripe_enabled) and has_secret and has_pub

    _write_file(data)
    return {
        "secret_updated": secret_updated,
        "webhook_updated": webhook_updated,
    }


def settings_for_form() -> Dict[str, Any]:
    merged = _merged()
    file_data = _read_file()
    return {
        "stripe_enabled": merged["stripe_enabled"],
        "publishable_key": get_publishable_key(),
        "has_secret": has_stored_secret(),
        "secret_saved_in_file": bool(_field_str(file_data.get("secret_key"))),
        "secret_from_env": bool(config.STRIPE_SECRET_KEY)
        and not _field_str(file_data.get("secret_key")),
        "has_webhook_secret": has_stored_webhook_secret(),
        "webhook_saved_in_file": bool(_field_str(file_data.get("webhook_secret"))),
        "webhook_from_env": bool(config.STRIPE_WEBHOOK_SECRET)
        and not _field_str(file_data.get("webhook_secret")),
        "card_surcharge_percent": surcharge_percent(),
        "xero_payment_account_code": merged["xero_payment_account_code"],
        "credentials_ok": has_credentials(),
        "webhook_url": "{0}/integrations/stripe/webhook".format(
            config.APP_BASE_URL.rstrip("/")
        ),
        "settings_path": str(SETTINGS_PATH.resolve()),
    }
