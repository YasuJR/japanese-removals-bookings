"""Bootstrap production secrets from environment variables."""

import json
from pathlib import Path

import config


def _write_json_env(env_key: str, path: str) -> bool:
    raw = (getattr(config, env_key, "") or "").strip()
    if not raw:
        return False
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and not config.PRODUCTION:
        return False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        target.write_text(raw, encoding="utf-8")
        return True
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True


def _write_google_credentials() -> None:
    if config.GOOGLE_OAUTH_JSON:
        _write_json_env("GOOGLE_OAUTH_JSON", config.GOOGLE_CREDENTIALS_FILE)
    elif config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
        path = Path(config.GOOGLE_CREDENTIALS_FILE)
        if not path.is_file():
            payload = {
                "web": {
                    "client_id": config.GOOGLE_CLIENT_ID,
                    "client_secret": config.GOOGLE_CLIENT_SECRET,
                    "redirect_uris": [config.GOOGLE_REDIRECT_URI],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def bootstrap_production() -> None:
    """Write OAuth token/credential files from env on ephemeral disks (Render)."""
    config.CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    _write_google_credentials()
    _write_json_env("GOOGLE_TOKEN_JSON", config.GOOGLE_TOKEN_FILE)
    _write_json_env("XERO_TOKEN_JSON", config.XERO_TOKEN_FILE)
    _bootstrap_stripe_settings()


def _bootstrap_stripe_settings() -> None:
    """Write stripe_settings.json from env on Render (ephemeral disk)."""
    if not config.PRODUCTION:
        return
    has_stripe_env = any(
        [
            config.STRIPE_PUBLISHABLE_KEY,
            config.STRIPE_SECRET_KEY,
            config.STRIPE_WEBHOOK_SECRET,
        ]
    )
    if not has_stripe_env:
        return
    from integrations import stripe_config

    merged = stripe_config.settings_for_form()
    env_publishable = (config.STRIPE_PUBLISHABLE_KEY or "").strip()
    if env_publishable and not stripe_config.publishable_key_valid(env_publishable):
        env_publishable = ""
    publishable_key = env_publishable or merged.get("publishable_key") or ""
    stripe_config.save_settings(
        stripe_enabled=config.STRIPE_ENABLED or merged.get("stripe_enabled", False),
        publishable_key=publishable_key,
        secret_key=config.STRIPE_SECRET_KEY or "",
        webhook_secret=config.STRIPE_WEBHOOK_SECRET or "",
        card_surcharge_percent=merged.get("card_surcharge_percent")
        or stripe_config.DEFAULT_SURCHARGE_PERCENT,
        xero_payment_account_code=merged.get("xero_payment_account_code") or "",
    )


def ensure_staff_user() -> None:
    """Create initial staff user when STAFF_USERNAME/STAFF_PASSWORD are set."""
    import logging

    logger = logging.getLogger(__name__)
    username = (config.STAFF_USERNAME or "").strip()
    password = (config.STAFF_PASSWORD or "").strip()
    if not username or not password:
        if config.PRODUCTION:
            logger.warning(
                "Staff bootstrap skipped — set STAFF_USERNAME and STAFF_PASSWORD on Render."
            )
        return
    import auth
    import database as db

    db.init_db()
    if db.staff_user_count() > 0:
        return
    user_id = db.create_staff_user(
        username,
        auth.hash_password(password),
        config.STAFF_DISPLAY_NAME or username,
    )
    logger.info("Created initial staff user id=%s username=%s", user_id, username)
