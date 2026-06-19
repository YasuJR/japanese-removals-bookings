"""Executive dashboard settings."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import config

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "executive_settings.json"
DEFAULT_MONTHLY_TARGET = 50000.0


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


def get_monthly_revenue_target() -> float:
    raw = _read_file().get("monthly_revenue_target")
    if raw in (None, ""):
        return DEFAULT_MONTHLY_TARGET
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_MONTHLY_TARGET


def save_monthly_revenue_target(value: float) -> None:
    data = _read_file()
    data["monthly_revenue_target"] = max(0.0, float(value))
    _write_file(data)


def settings_for_form() -> Dict[str, Any]:
    return {
        "monthly_revenue_target": get_monthly_revenue_target(),
    }
