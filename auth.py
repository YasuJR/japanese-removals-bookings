"""Staff login sessions."""

from functools import wraps
from typing import Any, Callable, Optional

from flask import flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import database as db


def hash_password(password: str) -> str:
    # pbkdf2:sha256 — works on all Python 3.9 builds (some Mac builds lack scrypt)
    return generate_password_hash(password, method="pbkdf2:sha256")


def verify_password(password_hash: str, password: str) -> bool:
    return check_password_hash(password_hash, password)


def login_user(user_id: int, username: str) -> None:
    session.clear()
    session["user_id"] = user_id
    session["username"] = username
    session.permanent = True


def logout_user() -> None:
    session.clear()


def get_current_user_id() -> Optional[int]:
    raw = session.get("user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def load_logged_in_user() -> None:
    user_id = get_current_user_id()
    g.user = db.get_staff_user(user_id) if user_id else None
    if user_id and g.user is None:
        logout_user()


def login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if g.get("user") is None:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped
