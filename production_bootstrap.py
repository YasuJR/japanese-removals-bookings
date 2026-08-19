"""Bootstrap production secrets from environment variables."""

import json
import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)


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
    # Xero tokens are migrated in bootstrap_xero_settings() after init_db so a
    # stale XERO_TOKEN_JSON env var cannot overwrite a live reconnect on disk.


def bootstrap_xero_settings() -> None:
    """
    Load Xero auth from PostgreSQL (production), migrating live JSON files or
    env vars into the database without deleting the files.
    """
    from integrations import xero_config

    stored = xero_config.read_stored_settings()
    if xero_config.has_credentials() or xero_config.has_stored_token():
        xero_config.restore_files_from_stored()
        if config.PRODUCTION:
            logger.info(
                "Xero authentication loaded from %s.",
                xero_config.storage_description(),
            )
        return
    if config.PRODUCTION:
        logger.warning(
            "Xero is not configured — connect in Settings → Xero or migrate "
            "credentials/xero_settings.json and credentials/xero_token.json "
            "into PostgreSQL."
        )


def bootstrap_stripe_settings() -> None:
    """Seed Stripe settings from env/JSON into PostgreSQL (survives redeploys)."""
    if not config.PRODUCTION:
        return

    from integrations import stripe_config

    raw_json = (config.STRIPE_SETTINGS_JSON or "").strip()
    if raw_json:
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.warning("STRIPE_SETTINGS_JSON is not valid JSON — skipping.")
        else:
            if isinstance(payload, dict):
                stripe_config.import_settings(payload)
                logger.info("Loaded Stripe settings from STRIPE_SETTINGS_JSON.")
                return

    stripe_config.sanitize_stored_settings()
    stored = stripe_config.read_stored_settings()
    has_secret = stripe_config.secret_key_valid(stored.get("secret_key"))
    if has_secret:
        stripe_config.merge_env_overrides()
        logger.info(
            "Stripe settings loaded from persistent storage (pk source: %s).",
            stripe_config.publishable_key_source(),
        )
        return

    stripe_config.seed_from_env()
    if stripe_config.secret_key_valid():
        logger.info("Seeded Stripe settings from environment variables.")
    else:
        logger.warning(
            "Stripe not configured — save in Settings → Stripe or set Render env vars."
        )


def ensure_staff_user() -> None:
    """Create initial staff user when STAFF_USERNAME/STAFF_PASSWORD are set."""
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
