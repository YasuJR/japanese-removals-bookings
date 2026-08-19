"""Persist Xero API credentials (PostgreSQL on Render, JSON files locally)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import config
import db_backend

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "xero_settings.json"
TOKEN_PATH = Path(config.XERO_TOKEN_FILE)
STORAGE_KEY = "xero"
SETTINGS_FILE_KEYS = (
    "client_id",
    "client_secret",
    "tenant_id",
    "auto_create_draft_on_confirmed",
    "auto_create_on_booking_create",
)


def _field_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _use_db_storage() -> bool:
    return bool(config.PRODUCTION and db_backend.is_postgres())


def _token_usable(token: Any) -> bool:
    if not isinstance(token, dict):
        return False
    return bool(_field_str(token.get("access_token")) and _field_str(token.get("refresh_token")))


def _read_settings_file() -> Dict[str, Any]:
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _read_token_file() -> Dict[str, Any]:
    if not TOKEN_PATH.is_file():
        return {}
    try:
        data = json.loads(TOKEN_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_settings_file(data: Dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: data[key] for key in SETTINGS_FILE_KEYS if key in data}
    SETTINGS_PATH.write_text(json.dumps(payload, indent=2))
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except OSError:
        pass


def _write_token_file(token: Dict[str, Any]) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(token, indent=2))
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass


def _read_file() -> Dict[str, Any]:
    """Combined on-disk settings + token (legacy layout, never deleted)."""
    data = dict(_read_settings_file())
    token = _read_token_file()
    if _token_usable(token):
        data["token"] = token
    elif token:
        data["token"] = token
    return data


def _write_file(data: Dict[str, Any]) -> None:
    """Write-through to the existing JSON files. Never deletes them."""
    _write_settings_file(data)
    token = data.get("token")
    if isinstance(token, dict) and (_token_usable(token) or token.get("access_token")):
        _write_token_file(token)


def _read_db() -> Dict[str, Any]:
    if not _use_db_storage():
        return {}
    import database as db

    data = db.get_integration_settings(STORAGE_KEY)
    return data if isinstance(data, dict) else {}


def _write_db(data: Dict[str, Any]) -> None:
    if not _use_db_storage():
        return
    import database as db

    db.save_integration_settings(STORAGE_KEY, data)


def _env_settings() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    client_id = _field_str(config.XERO_CLIENT_ID)
    client_secret = _field_str(config.XERO_CLIENT_SECRET)
    tenant_id = _field_str(config.XERO_TENANT_ID)
    if client_id:
        data["client_id"] = client_id
    if client_secret:
        data["client_secret"] = client_secret
    if tenant_id:
        data["tenant_id"] = tenant_id
    raw = _field_str(config.XERO_TOKEN_JSON)
    if raw:
        try:
            token = json.loads(raw)
        except json.JSONDecodeError:
            token = None
        if isinstance(token, dict) and token:
            data["token"] = token
    return data


def _fill_missing(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Copy only blank fields from incoming onto base. Never replace a non-empty value."""
    out = dict(base or {})
    for key in ("client_id", "client_secret", "tenant_id"):
        if not _field_str(out.get(key)) and _field_str(incoming.get(key)):
            out[key] = _field_str(incoming.get(key))
    if not _token_usable(out.get("token")) and _token_usable(incoming.get("token")):
        out["token"] = dict(incoming["token"])
    for key in ("auto_create_draft_on_confirmed", "auto_create_on_booking_create"):
        if key not in out and key in incoming:
            out[key] = incoming[key]
    return out


def _has_auth_payload(data: Dict[str, Any]) -> bool:
    return bool(
        _field_str(data.get("client_id"))
        or _field_str(data.get("client_secret"))
        or _field_str(data.get("tenant_id"))
        or _token_usable(data.get("token"))
    )


def read_stored_settings() -> Dict[str, Any]:
    """
    Load Xero auth. PostgreSQL is the source of truth in production;
    files and env fill any blanks and are migrated into Postgres when missing.
    """
    file_data = _read_file()
    env_data = _env_settings()
    if _use_db_storage():
        db_data = _read_db()
        merged = _fill_missing(dict(db_data), file_data)
        merged = _fill_missing(merged, env_data)
        if merged != db_data and _has_auth_payload(merged):
            _write_db(merged)
            logger.info(
                "Persisted Xero authentication to PostgreSQL (files left in place)."
            )
        return merged
    return _fill_missing(file_data, env_data)


def write_stored_settings(data: Dict[str, Any]) -> None:
    """Persist to PostgreSQL (production) and write-through to JSON files."""
    payload = dict(data or {})
    _write_db(payload)
    _write_file(payload)


def restore_files_from_stored() -> None:
    """Recreate JSON files from durable storage after an ephemeral-disk deploy."""
    stored = read_stored_settings()
    if _has_auth_payload(stored):
        _write_file(stored)


def get_token() -> Dict[str, Any]:
    token = read_stored_settings().get("token")
    return dict(token) if isinstance(token, dict) else {}


def save_token(token: Dict[str, Any]) -> None:
    """Replace the OAuth token and immediately persist (used on refresh)."""
    if not isinstance(token, dict) or not token:
        return
    stored = read_stored_settings()
    stored["token"] = dict(token)
    write_stored_settings(stored)


def _mask_secret(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "****"
    return value[:4] + "…" + value[-4:]


def get_client_id() -> str:
    stored = _field_str(read_stored_settings().get("client_id"))
    return stored or config.XERO_CLIENT_ID


def get_client_secret() -> str:
    stored = _field_str(read_stored_settings().get("client_secret"))
    return stored or config.XERO_CLIENT_SECRET


def get_tenant_id() -> str:
    stored = _field_str(read_stored_settings().get("tenant_id"))
    return stored or config.XERO_TENANT_ID


def has_stored_secret_in_file() -> bool:
    return bool(_field_str(_read_settings_file().get("client_secret")))


def has_stored_client_id_in_file() -> bool:
    return bool(_field_str(_read_settings_file().get("client_id")))


def has_stored_secret() -> bool:
    return bool(_field_str(read_stored_settings().get("client_secret"))) or bool(
        config.XERO_CLIENT_SECRET
    )


def has_credentials() -> bool:
    """
    Both Client ID and Client Secret are available (Postgres, file, and/or env).
    """
    return bool(get_client_id()) and bool(get_client_secret())


def has_stored_token() -> bool:
    return _token_usable(get_token())


def storage_description() -> str:
    if _use_db_storage():
        return "PostgreSQL (integration_settings.xero)"
    return "local JSON files"


def credentials_debug() -> Dict[str, Any]:
    """Diagnostic snapshot for settings UI (secrets masked)."""
    from integrations import xero

    stored = read_stored_settings()
    file_data = _read_settings_file()
    secret_in_file = _field_str(file_data.get("client_secret"))
    cid_in_file = _field_str(file_data.get("client_id"))
    token = stored.get("token") if isinstance(stored.get("token"), dict) else {}
    return {
        "settings_path": str(SETTINGS_PATH.resolve()),
        "token_path": str(TOKEN_PATH.resolve()),
        "file_exists": SETTINGS_PATH.is_file(),
        "token_file_exists": TOKEN_PATH.is_file(),
        "storage": storage_description(),
        "postgres": _use_db_storage(),
        "client_id_in_file": bool(cid_in_file),
        "secret_in_file": bool(secret_in_file),
        "client_id_resolved": bool(get_client_id()),
        "secret_resolved": bool(get_client_secret()),
        "token_stored": _token_usable(token),
        "credentials_ok": has_credentials(),
        "redirect_uri": xero.resolve_redirect_uri(config.XERO_REDIRECT_URI),
        "scopes": list(xero.XERO_SCOPES),
        "authorize_url": xero.authorize_url_preview(),
        "masked_json": {
            "client_id": _field_str(stored.get("client_id"))
            or cid_in_file
            or "(empty)",
            "client_secret": _mask_secret(
                _field_str(stored.get("client_secret")) or secret_in_file
            ),
            "tenant_id": _field_str(stored.get("tenant_id")) or "(empty)",
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
    Existing OAuth tokens are preserved.
    """
    existing = read_stored_settings()
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

    write_stored_settings(data)
    stored_secret = _field_str(data.get("client_secret"))
    return {
        "secret_updated": secret_updated,
        "secret_preserved": bool(stored_secret) and not secret_updated,
    }


def auto_create_draft_on_confirmed() -> bool:
    return bool(read_stored_settings().get("auto_create_draft_on_confirmed", False))


def auto_create_on_booking_create() -> bool:
    stored = read_stored_settings()
    if "auto_create_on_booking_create" in stored:
        return bool(stored.get("auto_create_on_booking_create"))
    return True


def settings_for_form() -> Dict[str, Any]:
    stored = read_stored_settings()
    file_data = _read_settings_file()
    secret_in_file = bool(_field_str(file_data.get("client_secret")))
    secret_in_store = bool(_field_str(stored.get("client_secret")))
    secret_in_env = bool(config.XERO_CLIENT_SECRET)
    return {
        "client_id": get_client_id(),
        "tenant_id": get_tenant_id(),
        "has_secret": has_stored_secret(),
        "secret_saved_in_file": secret_in_file or (secret_in_store and _use_db_storage()),
        "secret_from_env": secret_in_env and not secret_in_store,
        "credentials_ok": has_credentials(),
        "credentials_debug": credentials_debug(),
        "auto_create_draft_on_confirmed": auto_create_draft_on_confirmed(),
        "auto_create_on_booking_create": auto_create_on_booking_create(),
        "storage": storage_description(),
    }
