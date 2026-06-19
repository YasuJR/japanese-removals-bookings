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


def ensure_staff_user() -> None:
    """Create initial staff user when STAFF_USERNAME/STAFF_PASSWORD are set."""
    username = (config.STAFF_USERNAME or "").strip()
    password = (config.STAFF_PASSWORD or "").strip()
    if not username or not password:
        return
    import auth
    import database as db

    db.init_db()
    if db.staff_user_count() > 0:
        return
    db.create_staff_user(
        username,
        auth.hash_password(password),
        config.STAFF_DISPLAY_NAME or username,
    )
