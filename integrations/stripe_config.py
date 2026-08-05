"""Stripe payment settings — API keys and card surcharge (UI-editable JSON)."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import config
import db_backend

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "stripe_settings.json"
DEFAULT_SURCHARGE_PERCENT = 2.0


def _field_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _uses_postgres_storage() -> bool:
    return bool(config.PRODUCTION and db_backend.is_postgres())


def _read_postgres() -> Dict[str, Any]:
    try:
        import database as db

        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT data_json FROM stripe_settings WHERE singleton_id = 1"
            ).fetchone()
        if not row:
            return {}
        data = json.loads(row["data_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_postgres(data: Dict[str, Any]) -> None:
    import database as db

    payload = json.dumps(data, indent=2)
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO stripe_settings (singleton_id, data_json, updated_at)
            VALUES (1, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (singleton_id) DO UPDATE SET
                data_json = EXCLUDED.data_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (payload,),
        )


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


def _read_storage() -> Dict[str, Any]:
    if _uses_postgres_storage():
        return _read_postgres()
    return _read_file()


def _write_storage(data: Dict[str, Any]) -> None:
    if _uses_postgres_storage():
        _write_postgres(data)
        return
    _write_file(data)


def storage_description() -> str:
    if _uses_postgres_storage():
        return "PostgreSQL table stripe_settings"
    return str(SETTINGS_PATH.resolve())


def _merged() -> Dict[str, Any]:
    stored = _read_storage()
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


def _valid_env_publishable() -> str:
    key = config.STRIPE_PUBLISHABLE_KEY.strip()
    return key if publishable_key_valid(key) else ""


def _valid_env_secret() -> str:
    key = config.STRIPE_SECRET_KEY.strip()
    return key if secret_key_valid(key) else ""


def _valid_env_webhook() -> str:
    key = config.STRIPE_WEBHOOK_SECRET.strip()
    return key if webhook_secret_valid(key) else ""


def get_publishable_key() -> str:
    stored = _merged()["publishable_key"]
    if stored:
        return stored
    return _valid_env_publishable()


def get_publishable_key_for_form() -> str:
    """Stored publishable key only — never show invalid env fallbacks in the UI."""
    stored = _merged()["publishable_key"]
    if stored:
        return stored
    return _valid_env_publishable()


def get_secret_key() -> str:
    stored = _merged()["secret_key"]
    if stored:
        return stored
    return _valid_env_secret()


def get_webhook_secret() -> str:
    stored = _merged()["webhook_secret"]
    if stored:
        return stored
    return _valid_env_webhook()


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
    return bool(_field_str(_read_storage().get("secret_key"))) or bool(
        _valid_env_secret()
    )


def has_stored_webhook_secret() -> bool:
    return bool(_field_str(_read_storage().get("webhook_secret"))) or bool(
        _valid_env_webhook()
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
    return _field_str(existing), False


def merge_settings(updates: Dict[str, Any]) -> None:
    """Merge non-empty settings without clearing existing stored secrets."""
    existing = _read_storage()
    data = dict(existing)
    for key, value in updates.items():
        if value is None:
            continue
        if key in ("publishable_key", "secret_key", "webhook_secret", "xero_payment_account_code"):
            text = _field_str(value)
            if not text:
                continue
            if key == "publishable_key" and not publishable_key_valid(text):
                continue
            if key == "secret_key" and not secret_key_valid(text):
                continue
            if key == "webhook_secret" and not webhook_secret_valid(text):
                continue
            data[key] = text
        elif key == "stripe_enabled":
            data[key] = bool(value)
        elif key == "card_surcharge_percent":
            try:
                data[key] = round(float(value), 2)
            except (TypeError, ValueError):
                pass
    _write_storage(data)


def import_settings(payload: Dict[str, Any]) -> None:
    """Replace storage with a validated settings payload (bootstrap from env JSON)."""
    if not isinstance(payload, dict):
        return
    merge_settings(payload)


def save_settings(
    *,
    stripe_enabled: bool,
    publishable_key: str,
    secret_key: str = "",
    webhook_secret: str = "",
    card_surcharge_percent: float = DEFAULT_SURCHARGE_PERCENT,
    xero_payment_account_code: str = "",
) -> Dict[str, bool]:
    existing = _read_storage()
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

    _write_storage(data)
    return {
        "secret_updated": secret_updated,
        "webhook_updated": webhook_updated,
        "publishable_key_rejected": publishable_key_rejected,
    }


def settings_for_form() -> Dict[str, Any]:
    merged = _merged()
    stored = _read_storage()
    stored_secret = _field_str(stored.get("secret_key"))
    stored_webhook = _field_str(stored.get("webhook_secret"))
    env_secret = _valid_env_secret()
    env_webhook = _valid_env_webhook()
    return {
        "stripe_enabled": merged["stripe_enabled"],
        "publishable_key": get_publishable_key_for_form(),
        "has_secret": has_stored_secret(),
        "secret_saved_in_file": bool(stored_secret),
        "secret_from_env": bool(env_secret) and not stored_secret,
        "has_webhook_secret": has_stored_webhook_secret(),
        "webhook_saved_in_file": bool(stored_webhook),
        "webhook_from_env": bool(env_webhook) and not stored_webhook,
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
        "settings_path": storage_description(),
        "storage_backend": "postgres" if _uses_postgres_storage() else "file",
    }
