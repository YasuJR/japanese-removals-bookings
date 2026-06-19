"""Production configuration from environment variables."""

import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_bool(key: str, default: bool = False) -> bool:
    val = _env(key, "true" if default else "false").lower()
    return val in ("1", "true", "yes", "on")


def _env_path(key: str, default_relative: str) -> str:
    raw = _env(key, default_relative)
    path = Path(raw)
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path.resolve())


# Environment detection
RENDER = _env_bool("RENDER") or bool(_env("RENDER_EXTERNAL_URL"))
PRODUCTION = _env_bool("PRODUCTION") or RENDER
DATABASE_URL = _env("DATABASE_URL")
RENDER_EXTERNAL_URL = _env("RENDER_EXTERNAL_URL").rstrip("/")
PRODUCTION_DOMAIN = _env("PRODUCTION_DOMAIN")

# OAuth — HTTPS only in production
if PRODUCTION:
    os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
else:
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def _default_app_base_url() -> str:
    if PRODUCTION_DOMAIN:
        domain = PRODUCTION_DOMAIN.rstrip("/")
        if not domain.startswith("http"):
            domain = "https://{0}".format(domain)
        return domain
    if RENDER_EXTERNAL_URL:
        return RENDER_EXTERNAL_URL
    return "http://127.0.0.1:5001"


# Core
_DEFAULT_SECRET = "change-this-in-production-use-a-long-random-string"
SECRET_KEY = _env("SECRET_KEY", _DEFAULT_SECRET)
COMPANY_NAME = _env("COMPANY_NAME", "Japanese Removals")
COMPANY_LEGAL_NAME = _env("COMPANY_LEGAL_NAME", "JR West Pty Ltd")
COMPANY_PHONE = _env("COMPANY_PHONE") or "0481 089 573"
TIMEZONE = _env("TIMEZONE", "Australia/Perth")
APP_BASE_URL = _env("APP_BASE_URL") or _default_app_base_url()


def oauth_url(path: str) -> str:
    base = APP_BASE_URL.rstrip("/")
    if not path.startswith("/"):
        path = "/{0}".format(path)
    return "{0}{1}".format(base, path)


# Core continued — integrations below use oauth_url()

# Google Maps
GOOGLE_MAPS_API_KEY = _env("GOOGLE_MAPS_API_KEY")
DEFAULT_DRIVER_ORIGIN = _env(
    "DEFAULT_DRIVER_ORIGIN", "Perth, Western Australia, Australia"
)

# Google OAuth / Calendar / Gmail
GOOGLE_CALENDAR_ENABLED = _env_bool("GOOGLE_CALENDAR_ENABLED")
GMAIL_INBOX_ENABLED = _env_bool("GMAIL_INBOX_ENABLED")
GOOGLE_CREDENTIALS_FILE = _env_path(
    "GOOGLE_CREDENTIALS_FILE", "credentials/google_credentials.json"
)
GOOGLE_TOKEN_FILE = _env_path("GOOGLE_TOKEN_FILE", "credentials/google_token.json")
GOOGLE_OAUTH_JSON = _env("GOOGLE_OAUTH_JSON")
GOOGLE_TOKEN_JSON = _env("GOOGLE_TOKEN_JSON")
GOOGLE_CALENDAR_ID = _env("GOOGLE_CALENDAR_ID", "primary")
GOOGLE_CLIENT_ID = _env("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _env("GOOGLE_CLIENT_SECRET")
def _production_redirect(env_value: str, path: str) -> str:
    raw = (env_value or "").strip()
    canonical = oauth_url(path)
    if PRODUCTION and raw.startswith("http://"):
        return canonical
    return raw or canonical


GOOGLE_REDIRECT_URI = _production_redirect(
    _env("GOOGLE_REDIRECT_URI"),
    "/integrations/google/callback",
)
CALENDAR_ORGANIZER_EMAILS = [
    e.strip().lower()
    for e in _env(
        "CALENDAR_ORGANIZER_EMAILS", "sales.sugimoto@gmail.com"
    ).split(",")
    if e.strip()
]

# Twilio SMS
SMS_ENABLED = _env_bool("SMS_ENABLED")
SMS_ON_BOOKING_CREATE = _env_bool("SMS_ON_BOOKING_CREATE")
SMS_ON_BOOKING_UPDATE = _env_bool("SMS_ON_BOOKING_UPDATE")
TWILIO_ACCOUNT_SID = _env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _env("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = _env("TWILIO_FROM_NUMBER")

# SMTP email
EMAIL_ENABLED = _env_bool("EMAIL_ENABLED")
SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "587") or "587")
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
SMTP_USE_TLS = _env_bool("SMTP_USE_TLS", True)
EMAIL_FROM = _env("EMAIL_FROM")

# Xero
XERO_ENABLED = _env_bool("XERO_ENABLED")
XERO_CLIENT_ID = _env("XERO_CLIENT_ID")
XERO_CLIENT_SECRET = _env("XERO_CLIENT_SECRET")
XERO_REDIRECT_URI = _production_redirect(
    _env("XERO_REDIRECT_URI"),
    "/integrations/xero/callback",
)
XERO_TOKEN_FILE = _env_path("XERO_TOKEN_FILE", "credentials/xero_token.json")
XERO_TOKEN_JSON = _env("XERO_TOKEN_JSON")
XERO_TENANT_ID = _env("XERO_TENANT_ID")
XERO_DEFAULT_LINE_AMOUNT = float(_env("XERO_DEFAULT_LINE_AMOUNT", "0") or "0")

# Invoice defaults
INVOICE_DEFAULT_HOURLY_RATE = float(_env("INVOICE_DEFAULT_HOURLY_RATE", "180") or "180")
INVOICE_DEFAULT_CALLOUT_FEE = float(_env("INVOICE_DEFAULT_CALLOUT_FEE", "90") or "90")
INVOICE_GST_ENABLED_DEFAULT = _env_bool("INVOICE_GST_ENABLED_DEFAULT", True)
INVOICE_DUE_DAYS = int(_env("INVOICE_DUE_DAYS", "7") or "7")
INVOICE_BANK_ACCOUNT_NAME = _env("INVOICE_BANK_ACCOUNT_NAME", "JR West Pty Ltd")
INVOICE_BANK_BSB = _env("INVOICE_BANK_BSB", "036308")
INVOICE_BANK_ACCOUNT_NUMBER = _env("INVOICE_BANK_ACCOUNT_NUMBER", "405623")

# Profit dashboard
PROFIT_LABOUR_COST_PER_MOVER_HOUR = float(
    _env("PROFIT_LABOUR_COST_PER_MOVER_HOUR", "45") or "45"
)
PROFIT_FUEL_COST_PER_JOB = float(_env("PROFIT_FUEL_COST_PER_JOB", "30") or "30")
PROFIT_FUEL_COST_PER_HOUR = float(
    _env("PROFIT_FUEL_COST_PER_HOUR", "8") or "8"
)
PROFIT_SUPER_PCT = float(_env("PROFIT_SUPER_PCT", "11.5") or "11.5")
PROFIT_WORKERS_COMP_PCT = float(_env("PROFIT_WORKERS_COMP_PCT", "6") or "6")
PROFIT_MERCHANT_FEE_PCT = float(_env("PROFIT_MERCHANT_FEE_PCT", "1.5") or "1.5")

# Stripe
STRIPE_ENABLED = _env_bool("STRIPE_ENABLED")
STRIPE_PUBLISHABLE_KEY = _env("STRIPE_PUBLISHABLE_KEY")
STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET")

# Staff bootstrap (optional — first deploy)
STAFF_USERNAME = _env("STAFF_USERNAME")
STAFF_PASSWORD = _env("STAFF_PASSWORD")
STAFF_DISPLAY_NAME = _env("STAFF_DISPLAY_NAME", "Admin")

# Background job auth (Render cron → web health optional)
CRON_SECRET = _env("CRON_SECRET")

CREDENTIALS_DIR = BASE_DIR / "credentials"


def is_production() -> bool:
    return PRODUCTION


def production_checks() -> dict:
    parsed = urlparse(APP_BASE_URL)
    return {
        "production": PRODUCTION,
        "render": RENDER,
        "database_url_set": bool(DATABASE_URL),
        "app_base_url": APP_BASE_URL,
        "https": parsed.scheme == "https",
        "secret_key_ok": SECRET_KEY != _DEFAULT_SECRET and len(SECRET_KEY) >= 32,
        "oauth_callbacks": {
            "google": GOOGLE_REDIRECT_URI,
            "xero": XERO_REDIRECT_URI,
            "stripe_webhook": oauth_url("/integrations/stripe/webhook"),
            "twilio_status": oauth_url("/integrations/twilio/status"),
            "twilio_inbound": oauth_url("/integrations/twilio/inbound"),
        },
    }


def generate_secret_key() -> str:
    return secrets.token_urlsafe(48)
