"""Google review automation settings."""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

SETTINGS_PATH = Path(config.CREDENTIALS_DIR) / "review_settings.json"

CHANNEL_SMS = "sms"
CHANNEL_EMAIL = "email"
CHANNEL_SMS_OR_EMAIL = "sms_or_email"

CHANNEL_CHOICES = (
    (CHANNEL_SMS, "SMS only"),
    (CHANNEL_EMAIL, "Email only"),
    (CHANNEL_SMS_OR_EMAIL, "SMS, or email if no phone"),
)

PLACEHOLDER_HELP = (
    "{customer_name}, {first_name}, {company_name}, {review_link}, "
    "{review_confirm_link}, {google_review_link}"
)

DEFAULT_SMS_TEMPLATE = (
    "Hi {first_name},\n"
    "Thank you for choosing Japanese Removals.\n"
    "If you were happy with our service, could you please leave us a Google review?\n"
    "{google_review_link}\n"
    "Thank you,\n"
    "Yasu\n"
    "Japanese Removals"
)

DEFAULT_EMAIL_SUBJECT = "Thank you from {company_name}"

DEFAULT_EMAIL_BODY = """Hi {customer_name},

Thank you for choosing {company_name}.

Would you mind leaving us a Google Review?
{review_link}

We really appreciate your feedback.

{company_name}
"""


def _default_settings() -> Dict[str, Any]:
    return {
        "automation_enabled": True,
        "wait_hours": 24,
        "channel": CHANNEL_SMS_OR_EMAIL,
        "google_review_url": "",
        "google_review_count": None,
        "google_average_rating": None,
        "sms_template": DEFAULT_SMS_TEMPLATE,
        "email_subject": DEFAULT_EMAIL_SUBJECT,
        "email_body": DEFAULT_EMAIL_BODY,
    }


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


def _merged() -> Dict[str, Any]:
    defaults = _default_settings()
    stored = _read_file()
    out = dict(defaults)
    for key in defaults:
        if key in stored:
            out[key] = stored[key]
    channel = (out.get("channel") or CHANNEL_SMS_OR_EMAIL).strip()
    if channel not in {c[0] for c in CHANNEL_CHOICES}:
        channel = CHANNEL_SMS_OR_EMAIL
    out["channel"] = channel
    try:
        out["wait_hours"] = max(1, int(out.get("wait_hours") or 24))
    except (TypeError, ValueError):
        out["wait_hours"] = 24
    return out


def is_automation_enabled() -> bool:
    merged = _merged()
    if not merged.get("automation_enabled", True):
        return False
    if not (merged.get("google_review_url") or "").strip():
        return False
    return True


def get_wait_hours() -> int:
    return int(_merged().get("wait_hours") or 24)


def get_channel() -> str:
    return _merged().get("channel") or CHANNEL_SMS_OR_EMAIL


def get_google_review_url() -> str:
    return (_merged().get("google_review_url") or "").strip()


def get_google_review_count() -> Optional[int]:
    raw = _merged().get("google_review_count")
    if raw in (None, ""):
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def get_google_average_rating() -> Optional[float]:
    raw = _merged().get("google_average_rating")
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
        if 0 < value <= 5:
            return round(value, 1)
    except (TypeError, ValueError):
        pass
    return None


def get_sms_template() -> str:
    return (_merged().get("sms_template") or DEFAULT_SMS_TEMPLATE).strip()


def get_email_subject() -> str:
    return (_merged().get("email_subject") or DEFAULT_EMAIL_SUBJECT).strip()


def get_email_body() -> str:
    return (_merged().get("email_body") or DEFAULT_EMAIL_BODY).strip()


def save_settings(
    automation_enabled: bool,
    wait_hours: int,
    channel: str,
    google_review_url: str,
    sms_template: str,
    email_subject: str,
    email_body: str,
    google_review_count: str = "",
    google_average_rating: str = "",
) -> None:
    data = _merged()
    data["automation_enabled"] = automation_enabled
    data["wait_hours"] = max(1, int(wait_hours or 24))
    if channel in {c[0] for c in CHANNEL_CHOICES}:
        data["channel"] = channel
    data["google_review_url"] = google_review_url.strip()
    count_text = (google_review_count or "").strip()
    if count_text:
        try:
            data["google_review_count"] = max(0, int(count_text))
        except ValueError:
            pass
    else:
        data["google_review_count"] = None
    rating_text = (google_average_rating or "").strip()
    if rating_text:
        try:
            rating = float(rating_text)
            if 0 < rating <= 5:
                data["google_average_rating"] = round(rating, 1)
        except ValueError:
            pass
    else:
        data["google_average_rating"] = None
    if sms_template.strip():
        data["sms_template"] = sms_template.strip()
    if email_subject.strip():
        data["email_subject"] = email_subject.strip()
    if email_body.strip():
        data["email_body"] = email_body.strip()
    _write_file(data)


def settings_for_form() -> Dict[str, Any]:
    merged = _merged()
    return {
        "automation_enabled": merged["automation_enabled"],
        "wait_hours": merged["wait_hours"],
        "channel": merged["channel"],
        "google_review_url": merged["google_review_url"],
        "google_review_count": merged.get("google_review_count"),
        "google_average_rating": merged.get("google_average_rating"),
        "sms_template": get_sms_template(),
        "email_subject": get_email_subject(),
        "email_body": get_email_body(),
        "placeholder_help": PLACEHOLDER_HELP,
        "channel_choices": CHANNEL_CHOICES,
    }
