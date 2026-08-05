"""Stripe payment settings — API keys and card surcharge (UI-editable JSON)."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import config
import db_backend

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "stripe_settings.json"
STORAGE_KEY = "stripe"
DEFAULT_SURCHARGE_PERCENT = 2.0


def _field_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _use_db_storage() -> bool:
    return bool(config.PRODUCTION and db_backend.is_postgres())


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


def _read_db() -> Dict[str, Any]:
    if not _use_db_storage():
        return {}
    import database as db

    return db.get_integration_settings(STORAGE_KEY)


def _write_db(data: Dict[str, Any]) -> None:
    if not _use_db_storage():
        return
    import database as db

    db.save_integration_settings(STORAGE_KEY, data)


def read_stored_settings() -> Dict[str, Any]:
    """Load persisted Stripe settings (PostgreSQL on Render, file locally)."""
    if _use_db_storage():
        data = _read_db()
        if data:
            return data
        file_data = _read_file()
        if file_data:
            _write_db(file_data)
            return file_data
        return {}
    return _read_file()


def write_stored_settings(data: Dict[str, Any]) -> None:
    """Persist Stripe settings to durable storage."""
    _write_db(data)
    _write_file(data)


def _merged() -> Dict[str, Any]:
    stored = read_stored_settings()
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
    stored = _merged()["publishable_key"]
    if stored:
        return stored
    env = _field_str(config.STRIPE_PUBLISHABLE_KEY)
    return env if publishable_key_valid(env) else ""


def get_secret_key() -> str:
    stored = _merged()["secret_key"]
    if stored:
        return stored
    env = _field_str(config.STRIPE_SECRET_KEY)
    return env if secret_key_valid(env) else ""


def get_webhook_secret() -> str:
    stored = _merged()["webhook_secret"]
    if stored:
        return stored
    env = _field_str(config.STRIPE_WEBHOOK_SECRET)
    return env if webhook_secret_valid(env) else ""


def publishable_key_valid(value: Optional[str] = None) -> bool:
    key = (value if value is not None else get_publishable_key()).strip()
    return key.startswith(("pk_live_", "pk_test_")) and len(key) >= 32


def secret_key_valid(value: Optional[str] = None) -> bool:
    key = (value if value is not None else get_secret_key()).strip()
    return key.startswith(("sk_live_", "sk_test_", "rk_live_", "rk_test_")) and len(
        key
    ) >= 32


def webhook_secret_valid(value: Optional[str] = None) -> bool:
    key = (value if value is not None else get_webhook_secret()).strip()
    if not key.startswith("whsec_") or len(key) < 32:
        return False
    lowered = key.lower()
    if "local_e2e" in lowered or lowered.endswith("_test_secret"):
        return False
    return True


def is_enabled() -> bool:
    if not (_merged()["stripe_enabled"] or config.STRIPE_ENABLED):
        return False
    return bool(get_publishable_key()) and bool(get_secret_key())


def has_stored_secret() -> bool:
    return bool(_field_str(read_stored_settings().get("secret_key"))) or secret_key_valid(
        config.STRIPE_SECRET_KEY
    )


def has_stored_webhook_secret() -> bool:
    return bool(_field_str(read_stored_settings().get("webhook_secret"))) or webhook_secret_valid(
        config.STRIPE_WEBHOOK_SECRET
    )


def has_credentials() -> bool:
    return secret_key_valid() and publishable_key_valid()


def is_ready() -> bool:
    return checkout_ready()


def checkout_ready() -> bool:
    """Server-side Checkout only needs the secret key."""
    if not (_merged()["stripe_enabled"] or config.STRIPE_ENABLED):
        return False
    return secret_key_valid()


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


def resolve_publishable_key(value: str, existing: Optional[str] = None) -> Tuple[str, bool]:
    """Return publishable key to store; reject invalid values (e.g. mk_… typos)."""
    candidate = (value or "").strip()
    if publishable_key_valid(candidate):
        return candidate, False
    if candidate:
        return _field_str(existing), True
    return "", False


def import_settings(data: Dict[str, Any]) -> None:
    if not isinstance(data, dict):
        return
    write_stored_settings(data)


def seed_from_env() -> None:
    """Seed empty storage from valid Render env vars."""
    data: Dict[str, Any] = {}
    if publishable_key_valid(config.STRIPE_PUBLISHABLE_KEY):
        data["publishable_key"] = _field_str(config.STRIPE_PUBLISHABLE_KEY)
    if secret_key_valid(config.STRIPE_SECRET_KEY):
        data["secret_key"] = _field_str(config.STRIPE_SECRET_KEY)
    if webhook_secret_valid(config.STRIPE_WEBHOOK_SECRET):
        data["webhook_secret"] = _field_str(config.STRIPE_WEBHOOK_SECRET)
    if not data:
        return
    existing = read_stored_settings()
    existing.update(data)
    if config.STRIPE_ENABLED:
        existing["stripe_enabled"] = True
    write_stored_settings(existing)


def merge_env_overrides() -> None:
    """Fill missing stored fields from valid env vars without wiping saved values."""
    existing = dict(read_stored_settings())
    if not existing:
        seed_from_env()
        return
    changed = False
    if publishable_key_valid(config.STRIPE_PUBLISHABLE_KEY):
        pk = _field_str(config.STRIPE_PUBLISHABLE_KEY)
        if pk != _field_str(existing.get("publishable_key")):
            existing["publishable_key"] = pk
            changed = True
    if secret_key_valid(config.STRIPE_SECRET_KEY) and not _field_str(
        existing.get("secret_key")
    ):
        existing["secret_key"] = _field_str(config.STRIPE_SECRET_KEY)
        changed = True
    if webhook_secret_valid(config.STRIPE_WEBHOOK_SECRET) and not _field_str(
        existing.get("webhook_secret")
    ):
        existing["webhook_secret"] = _field_str(config.STRIPE_WEBHOOK_SECRET)
        changed = True
    if config.STRIPE_ENABLED and not existing.get("stripe_enabled"):
        existing["stripe_enabled"] = True
        changed = True
    if changed:
        write_stored_settings(existing)


def save_settings(
    *,
    stripe_enabled: bool,
    publishable_key: str,
    secret_key: str = "",
    webhook_secret: str = "",
    card_surcharge_percent: float = DEFAULT_SURCHARGE_PERCENT,
    xero_payment_account_code: str = "",
) -> Dict[str, bool]:
    existing = read_stored_settings()
    data = dict(existing)
    resolved_pk, publishable_key_rejected = resolve_publishable_key(
        publishable_key, _field_str(existing.get("publishable_key"))
    )
    data["publishable_key"] = resolved_pk
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

    has_secret = secret_key_valid(_field_str(data.get("secret_key")) or config.STRIPE_SECRET_KEY)
    data["stripe_enabled"] = bool(stripe_enabled) and has_secret

    write_stored_settings(data)
    return {
        "secret_updated": secret_updated,
        "webhook_updated": webhook_updated,
        "publishable_key_rejected": publishable_key_rejected,
    }


def settings_for_form() -> Dict[str, Any]:
    merged = _merged()
    stored = read_stored_settings()
    stored_secret = _field_str(stored.get("secret_key"))
    stored_webhook = _field_str(stored.get("webhook_secret"))
    env_secret = secret_key_valid(config.STRIPE_SECRET_KEY)
    env_webhook = webhook_secret_valid(config.STRIPE_WEBHOOK_SECRET)
    storage_label = "database" if _use_db_storage() else "file"
    return {
        "stripe_enabled": merged["stripe_enabled"],
        "publishable_key": get_publishable_key(),
        "has_secret": has_stored_secret(),
        "secret_saved_in_storage": bool(stored_secret),
        "secret_saved_in_file": bool(_field_str(_read_file().get("secret_key"))),
        "secret_from_env": env_secret and not stored_secret,
        "has_webhook_secret": has_stored_webhook_secret(),
        "webhook_saved_in_storage": bool(stored_webhook),
        "webhook_saved_in_file": bool(_field_str(_read_file().get("webhook_secret"))),
        "webhook_from_env": env_webhook and not stored_webhook,
        "card_surcharge_percent": surcharge_percent(),
        "xero_payment_account_code": merged["xero_payment_account_code"],
        "credentials_ok": has_credentials(),
        "can_enable_stripe": secret_key_valid(),
        "checkout_ready": checkout_ready(),
        "publishable_key_valid": publishable_key_valid(),
        "secret_key_valid": secret_key_valid(),
        "webhook_secret_valid": webhook_secret_valid(),
        "webhook_url": "{0}/integrations/stripe/webhook".format(
            config.APP_BASE_URL.rstrip("/")
        ),
        "settings_path": "{0} ({1})".format(SETTINGS_PATH.resolve(), storage_label),
    }
