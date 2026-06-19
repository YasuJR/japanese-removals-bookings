"""Gmail inbox automation settings."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import config
from integrations import company_config, google_oauth

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "gmail_settings.json"


def _default_settings() -> Dict[str, Any]:
    company = company_config.get_settings()
    return {
        "automation_enabled": False,
        "inbox_query": "is:unread",
        "admin_notify_email": company.get("company_email") or "",
        "last_checked_at": "",
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


def _merged() -> Dict[str, Any]:
    defaults = _default_settings()
    stored = _read_file()
    return {
        "automation_enabled": stored.get(
            "automation_enabled", defaults["automation_enabled"]
        ),
        "inbox_query": (stored.get("inbox_query") or defaults["inbox_query"]).strip(),
        "admin_notify_email": (
            stored.get("admin_notify_email") or defaults["admin_notify_email"]
        ).strip(),
        "last_checked_at": (stored.get("last_checked_at") or "").strip(),
    }


def is_automation_enabled() -> bool:
    if not config.GMAIL_INBOX_ENABLED:
        return False
    return bool(_merged().get("automation_enabled"))


def inbox_query() -> str:
    return _merged().get("inbox_query") or "is:unread"


def admin_notify_email() -> str:
    email = (_merged().get("admin_notify_email") or "").strip()
    if email:
        return email
    return (company_config.get_settings().get("company_email") or "").strip()


def settings_for_form() -> Dict[str, Any]:
    merged = _merged()
    return {
        **merged,
        "settings_path": str(SETTINGS_PATH.resolve()),
        "gmail_scope_granted": google_oauth.gmail_scope_granted(),
        "google_token_present": google_oauth.is_token_present(),
    }


def save_settings(
    automation_enabled: bool,
    inbox_query: str,
    admin_notify_email: str,
) -> None:
    current = _merged()
    current["automation_enabled"] = bool(automation_enabled)
    current["inbox_query"] = (inbox_query or "is:unread").strip() or "is:unread"
    current["admin_notify_email"] = (admin_notify_email or "").strip()
    _write_file(current)


def touch_last_checked() -> None:
    current = _merged()
    current["last_checked_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    _write_file(current)
