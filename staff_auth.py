"""Staff Portal login — independent from office/admin sessions."""

import hmac
import os
from functools import wraps
from typing import Any, Callable, Dict, Optional

from flask import current_app, redirect, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.wrappers import Response

COOKIE_NAME = "jr_staff_portal"
COOKIE_PATH = "/staff"
_SERIALIZER_SALT = "japanese-removals-staff-portal"


def staff_portal_password() -> str:
    """Read Staff Portal password from the server environment (never cache)."""
    return os.environ.get("STAFF_PORTAL_PASSWORD", "").strip()


def _admin_bootstrap_password() -> str:
    return os.environ.get("STAFF_PASSWORD", "").strip()


def configured_staff_password() -> str:
    """Return the Staff Portal password, or empty if missing or same as admin bootstrap."""
    portal = staff_portal_password()
    if not portal:
        return ""
    admin_bootstrap = _admin_bootstrap_password()
    if admin_bootstrap and portal == admin_bootstrap:
        return ""
    return portal


def verify_staff_password(password: Any) -> bool:
    expected = configured_staff_password()
    provided = str(password or "")
    if not expected or not provided:
        return False
    return hmac.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )


def _serializer() -> URLSafeTimedSerializer:
    import config

    return URLSafeTimedSerializer(str(config.SECRET_KEY), salt=_SERIALIZER_SALT)


def _max_age_seconds() -> int:
    try:
        return max(int(current_app.permanent_session_lifetime.total_seconds()), 60)
    except RuntimeError:
        return 14 * 24 * 60 * 60


def _staff_session_payload() -> Optional[Dict[str, Any]]:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return None
    try:
        payload = _serializer().loads(token, max_age=_max_age_seconds())
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("portal") != "staff":
        return None
    return payload


def logged_in_staff_name() -> str:
    """Crew name bound to the Staff Portal cookie. Empty when unidentified."""
    payload = _staff_session_payload()
    if not payload:
        return ""
    return str(payload.get("staff") or "").strip()


def logged_in_staff_id() -> str:
    payload = _staff_session_payload()
    if not payload:
        return ""
    return str(payload.get("staff_id") or "").strip()


def is_staff_logged_in() -> bool:
    return bool(logged_in_staff_name())


def attach_staff_session(
    response: Response,
    staff_name: str,
    staff_id: Any = None,
) -> Response:
    import config

    payload = {
        "portal": "staff",
        "staff": str(staff_name or "").strip(),
    }
    if staff_id not in (None, ""):
        payload["staff_id"] = str(staff_id)
    token = _serializer().dumps(payload)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=_max_age_seconds(),
        httponly=True,
        secure=bool(config.PRODUCTION),
        samesite="Lax",
        path=COOKIE_PATH,
    )
    return response


def clear_staff_session(response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
    return response


def safe_staff_next(value: Any) -> str:
    """Allow only Staff Portal paths (never admin URLs)."""
    text = str(value or "").strip()
    if not text.startswith("/staff"):
        return "/staff"
    if text.startswith("//") or "://" in text or "\\" in text:
        return "/staff"
    if text.startswith("/staff/login") or text.startswith("/staff/logout"):
        return "/staff"
    if text == "/staff" or text.startswith("/staff?") or text.startswith("/staff/"):
        return text
    return "/staff"


def staff_login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if not is_staff_logged_in():
            nxt = request.full_path
            if nxt.endswith("?"):
                nxt = nxt[:-1]
            return redirect(url_for("staff_login", next=nxt))
        return view(*args, **kwargs)

    return wrapped
