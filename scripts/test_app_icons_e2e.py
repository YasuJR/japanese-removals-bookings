#!/usr/bin/env python3
"""Tests for favicon and Apple Touch Icon setup."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")


def test_icon_files_exist_with_correct_sizes():
    from PIL import Image

    apple = Image.open(ROOT / "static" / "apple-touch-icon.png")
    assert apple.size == (180, 180)

    fav16 = Image.open(ROOT / "static" / "favicon-16x16.png")
    assert fav16.size == (16, 16)

    fav32 = Image.open(ROOT / "static" / "favicon-32x32.png")
    assert fav32.size == (32, 32)

    assert (ROOT / "static" / "favicon.ico").is_file()
    return True


def test_head_icons_partial():
    html = (ROOT / "templates" / "_head_icons.html").read_text()
    assert 'rel="apple-touch-icon"' in html
    assert 'sizes="180x180"' in html
    assert "apple-touch-icon.png" in html
    assert "favicon.ico" in html
    assert "favicon-32x32.png" in html
    assert "favicon-16x16.png" in html
    return True


def test_base_and_login_include_icons():
    import auth
    import database as db
    from app import app

    db.init_db()
    uid = db.create_staff_user(
        "icon-test-{0}".format(os.getpid()),
        auth.hash_password("test"),
        "Icon Test",
    )
    client = app.test_client()

    login = client.get("/login")
    assert login.status_code == 200
    login_html = login.get_data(as_text=True)
    assert 'rel="apple-touch-icon"' in login_html
    assert "apple-touch-icon.png" in login_html

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = "icon-test"
    home = client.get("/dashboard")
    assert home.status_code == 200
    home_html = home.get_data(as_text=True)
    assert 'rel="apple-touch-icon"' in home_html
    assert "favicon-32x32.png" in home_html
    return True


def test_static_icon_routes():
    import auth
    import database as db
    from app import app

    db.init_db()
    client = app.test_client()
    for path in (
        "/static/apple-touch-icon.png",
        "/static/favicon.ico",
        "/static/favicon-32x32.png",
        "/static/favicon-16x16.png",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.content_length > 0, path
    return True


def main():
    tests = [
        test_icon_files_exist_with_correct_sizes,
        test_head_icons_partial,
        test_base_and_login_include_icons,
        test_static_icon_routes,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS:", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL:", fn.__name__, exc)
    print("\n{0}/{1} passed".format(len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
