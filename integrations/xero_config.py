"""Persist Xero API credentials (UI-editable, stored outside .env)."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import config

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "xero_settings.json"


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


def _mask_secret(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "****"
    return value[:4] + "…" + value[-4:]


def get_client_id() -> str:
    stored = _field_str(_read_file().get("client_id"))
    return stored or config.XERO_CLIENT_ID


def get_client_secret() -> str:
    stored = _field_str(_read_file().get("client_secret"))
    return stored or config.XERO_CLIENT_SECRET


def get_tenant_id() -> str:
    stored = _field_str(_read_file().get("tenant_id"))
    return stored or config.XERO_TENANT_ID


def has_stored_secret_in_file() -> bool:
    return bool(_field_str(_read_file().get("client_secret")))


def has_stored_client_id_in_file() -> bool:
    return bool(_field_str(_read_file().get("client_id")))


def has_stored_secret() -> bool:
    return has_stored_secret_in_file() or bool(config.XERO_CLIENT_SECRET)


def has_credentials() -> bool:
    """
    Both Client ID and Client Secret are available (settings file and/or .env).
    Single source of truth for UI and OAuth.
    """
    return bool(get_client_id()) and bool(get_client_secret())


def credentials_debug() -> Dict[str, Any]:
    """Diagnostic snapshot for settings UI (secrets masked)."""
    from integrations import xero

    file_data = _read_file()
    secret_in_file = _field_str(file_data.get("client_secret"))
    cid_in_file = _field_str(file_data.get("client_id"))
    return {
        "settings_path": str(SETTINGS_PATH.resolve()),
        "file_exists": SETTINGS_PATH.is_file(),
        "client_id_in_file": bool(cid_in_file),
        "secret_in_file": bool(secret_in_file),
        "client_id_resolved": bool(get_client_id()),
        "secret_resolved": bool(get_client_secret()),
        "credentials_ok": has_credentials(),
        "redirect_uri": xero.resolve_redirect_uri(config.XERO_REDIRECT_URI),
        "scopes": list(xero.XERO_SCOPES),
        "authorize_url": xero.authorize_url_preview(),
        "masked_json": {
            "client_id": cid_in_file or "(empty)",
            "client_secret": _mask_secret(secret_in_file),
            "tenant_id": _field_str(file_data.get("tenant_id")) or "(empty)",
        },
    }


def save_settings(
    client_id: str,
    client_secret: str = "",
    tenant_id: str = "",
    *,
    auto_create_draft_on_confirmed: Optional[bool] = None,
    auto_create_on_booking_create: Optional[bool] = None,
) -> Dict[str, str]:
    """
    Save Xero settings. Returns flags describing what changed.
    Client secret is only updated when a new non-empty value is submitted;
    otherwise the existing stored secret is preserved explicitly.
    """
    existing = _read_file()
    data = dict(existing)
    data["client_id"] = client_id.strip()
    data["tenant_id"] = tenant_id.strip()

    secret_updated = False
    new_secret = (client_secret or "").strip()
    existing_secret = _field_str(existing.get("client_secret"))

    if new_secret:
        data["client_secret"] = new_secret
        secret_updated = True
    elif existing_secret:
        data["client_secret"] = existing_secret

    if auto_create_draft_on_confirmed is not None:
        data["auto_create_draft_on_confirmed"] = bool(auto_create_draft_on_confirmed)
    if auto_create_on_booking_create is not None:
        data["auto_create_on_booking_create"] = bool(auto_create_on_booking_create)

    _write_file(data)
    stored_secret = _field_str(data.get("client_secret"))
    return {
        "secret_updated": secret_updated,
        "secret_preserved": bool(stored_secret) and not secret_updated,
    }


def auto_create_draft_on_confirmed() -> bool:
    return bool(_read_file().get("auto_create_draft_on_confirmed", False))


def auto_create_on_booking_create() -> bool:
    stored = _read_file()
    if "auto_create_on_booking_create" in stored:
        return bool(stored.get("auto_create_on_booking_create"))
    return True


def settings_for_form() -> Dict[str, Any]:
    file_data = _read_file()
    secret_in_file = bool(_field_str(file_data.get("client_secret")))
    secret_in_env = bool(config.XERO_CLIENT_SECRET)
    return {
        "client_id": get_client_id(),
        "tenant_id": get_tenant_id(),
        "has_secret": has_stored_secret(),
        "secret_saved_in_file": secret_in_file,
        "secret_from_env": secret_in_env and not secret_in_file,
        "credentials_ok": has_credentials(),
        "credentials_debug": credentials_debug(),
        "auto_create_draft_on_confirmed": auto_create_draft_on_confirmed(),
        "auto_create_on_booking_create": auto_create_on_booking_create(),
    }
