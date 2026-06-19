"""Shared Google OAuth credentials for Calendar and Gmail."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import config

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

SCOPES = [CALENDAR_SCOPE, GMAIL_SCOPE]
CALLBACK_PATH = "/integrations/google/callback"


def _load_credentials_file() -> Dict[str, Any]:
    path = Path(config.GOOGLE_CREDENTIALS_FILE)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_client_id() -> str:
    env_id = (config.GOOGLE_CLIENT_ID or "").strip()
    if env_id:
        return env_id
    data = _load_credentials_file()
    web = data.get("web") or data.get("installed") or {}
    return (web.get("client_id") or "").strip()


def credentials_redirect_uris() -> List[str]:
    data = _load_credentials_file()
    web = data.get("web") or data.get("installed") or {}
    uris = web.get("redirect_uris") or []
    return [str(uri).strip() for uri in uris if str(uri).strip()]


def resolve_redirect_uri(redirect_uri: str = "") -> str:
    """
    Return the redirect URI sent to Google.

    Must match an Authorized redirect URI in Google Cloud Console exactly.
    Do not derive from the browser host (localhost vs 127.0.0.1 mismatch).
    """
    raw = (redirect_uri or config.GOOGLE_REDIRECT_URI).strip()
    if not raw:
        raw = "http://127.0.0.1:5001{0}".format(CALLBACK_PATH)
    parts = urlparse(raw)
    if not parts.scheme or not parts.netloc:
        raise ValueError("GOOGLE_REDIRECT_URI must be an absolute URL.")
    path = parts.path or CALLBACK_PATH
    if not path.endswith(CALLBACK_PATH):
        path = CALLBACK_PATH
    return urlunparse((parts.scheme, parts.netloc, path, "", "", ""))


def normalize_authorization_response(
    authorization_response: str,
    redirect_uri: str = "",
) -> str:
    """Ensure callback URL uses the same redirect URI host as the authorize request."""
    canonical = resolve_redirect_uri(redirect_uri)
    parsed = urlparse(authorization_response)
    canonical_parts = urlparse(canonical)
    return urlunparse(
        (
            canonical_parts.scheme,
            canonical_parts.netloc,
            parsed.path or canonical_parts.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def oauth_config_debug(request_redirect_uri: str = "") -> Dict[str, Any]:
    """Diagnostics for OAuth redirect mismatch troubleshooting."""
    configured = resolve_redirect_uri()
    registered = credentials_redirect_uris()
    return {
        "client_id": get_client_id(),
        "redirect_uri_sent": configured,
        "redirect_uri_requested": (request_redirect_uri or "").strip(),
        "config_google_redirect_uri": config.GOOGLE_REDIRECT_URI,
        "callback_path": CALLBACK_PATH,
        "registered_redirect_uris": registered,
        "redirect_uri_registered": configured in registered,
        "scopes": list(SCOPES),
    }


def _ensure_oauth_local_http() -> None:
    import os

    if config.PRODUCTION:
        os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
    else:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def clear_stored_token() -> None:
    """Remove saved token so a fresh OAuth link can be established."""
    token_path = Path(config.GOOGLE_TOKEN_FILE)
    if token_path.is_file():
        token_path.unlink()


def _load_token_data() -> dict:
    token_path = Path(config.GOOGLE_TOKEN_FILE)
    if not token_path.is_file():
        return {}
    try:
        return json.loads(token_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def scope_granted(scope_url: str) -> bool:
    data = _load_token_data()
    scopes = data.get("scopes") or data.get("scope") or ""
    if isinstance(scopes, list):
        granted = scopes
    else:
        granted = str(scopes).split()
    return scope_url in granted


def gmail_scope_granted() -> bool:
    return scope_granted(GMAIL_SCOPE)


def calendar_scope_granted() -> bool:
    return scope_granted(CALENDAR_SCOPE)


def get_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    token_path = Path(config.GOOGLE_TOKEN_FILE)
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        return creds

    return None


def _make_flow(redirect_uri: str):
    _ensure_oauth_local_http()
    from google_auth_oauthlib.flow import Flow

    oauth_json = (config.GOOGLE_OAUTH_JSON or "").strip()
    if oauth_json:
        client_config = json.loads(oauth_json)
        return Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )
    if config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
        client_config = {
            "web": {
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "redirect_uris": [redirect_uri],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        return Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )
    return Flow.from_client_secrets_file(
        config.GOOGLE_CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=False,
    )


def begin_oauth(redirect_uri: str) -> Tuple[str, str]:
    """Return (authorization_url, state) for Flask session."""
    clear_stored_token()
    flow = _make_flow(redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    return auth_url, state


def complete_oauth(redirect_uri: str, authorization_response: str) -> bool:
    """Exchange auth code for tokens using client_secret (no PKCE)."""
    redirect_uri = resolve_redirect_uri(redirect_uri)
    authorization_response = normalize_authorization_response(
        authorization_response, redirect_uri
    )
    flow = _make_flow(redirect_uri)
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials
    token_path = Path(config.GOOGLE_TOKEN_FILE)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    return True


def is_token_present() -> bool:
    return Path(config.GOOGLE_TOKEN_FILE).is_file()
