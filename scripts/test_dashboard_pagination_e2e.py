#!/usr/bin/env python3
"""E2E tests for Dashboard jobs pagination."""

import html as html_module
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
from app import app
from dashboard_data import (
    DASHBOARD_JOBS_INITIAL,
    DASHBOARD_JOBS_PAGE_SIZE,
    paginate_dashboard_jobs,
    parse_jobs_limit,
)


_test_client_counter = 0


def _login_client():
    global _test_client_counter
    _test_client_counter += 1
    db.init_db()
    label = "dash-page-{0}-{1}".format(os.getpid(), _test_client_counter)
    uid = db.create_staff_user(
        label,
        auth.hash_password("test"),
        "Dash Page Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def test_parse_jobs_limit_helpers():
    assert parse_jobs_limit("", 100) == DASHBOARD_JOBS_INITIAL
    assert parse_jobs_limit("80", 100) == 80
    assert parse_jobs_limit("999", 50) == 50
    assert parse_jobs_limit("bad", 10) == min(DASHBOARD_JOBS_INITIAL, 10)

    jobs = list(range(100))
    visible, total, has_more, next_limit = paginate_dashboard_jobs(jobs, 40)
    assert len(visible) == 40
    assert total == 100
    assert has_more is True
    assert next_limit == 80
    visible2, _, has_more2, _ = paginate_dashboard_jobs(jobs, 80)
    assert len(visible2) == 80
    assert has_more2 is True
    visible3, _, has_more3, _ = paginate_dashboard_jobs(jobs, 100)
    assert len(visible3) == 100
    assert has_more3 is False
    return True


def test_dashboard_initial_row_cap():
    client = _login_client()
    html = client.get("/dashboard?filter=all").get_data(as_text=True)
    rows = len(re.findall(r'class="customer-link"', html))
    assert rows <= DASHBOARD_JOBS_INITIAL, rows
    assert "dashboard-sheet" in html
    assert ">Details</a>" in html
    assert ">Edit</a>" in html
    return True


def test_dashboard_load_more_increases_rows():
    client = _login_client()
    html = client.get("/dashboard?filter=all").get_data(as_text=True)
    rows_before = len(re.findall(r'class="customer-link"', html))
    if "Load more" not in html:
        print("SKIP load more: fewer than {0} bookings".format(DASHBOARD_JOBS_INITIAL + 1))
        return True
    match = re.search(r'href="([^"]*jobs_limit=\d+[^"]*)"', html)
    assert match, "Load more URL missing"
    path = html_module.unescape(match.group(1))
    html_more = client.get(path).get_data(as_text=True)
    rows_after = len(re.findall(r'class="customer-link"', html_more))
    assert rows_after > rows_before, (rows_before, rows_after)
    assert rows_after <= rows_before + DASHBOARD_JOBS_PAGE_SIZE
    return True


def test_dashboard_filters_reset_pagination():
    client = _login_client()
    html_wide = client.get(
        "/dashboard?filter=all&jobs_limit={0}".format(DASHBOARD_JOBS_INITIAL + 20)
    ).get_data(as_text=True)
    rows_wide = len(re.findall(r'class="customer-link"', html_wide))
    assert rows_wide >= DASHBOARD_JOBS_INITIAL

    html_reset = client.get("/dashboard?filter=all").get_data(as_text=True)
    rows_reset = len(re.findall(r'class="customer-link"', html_reset))
    assert rows_reset <= DASHBOARD_JOBS_INITIAL
    return True


def test_dashboard_filter_pills_present():
    client = _login_client()
    html = client.get("/dashboard").get_data(as_text=True)
    for label in ("All", "Today", "Upcoming", "Completed", "Paid", "Cancelled"):
        assert label in html
    assert "mobile.css" in html
    return True


def main():
    tests = [
        test_parse_jobs_limit_helpers,
        test_dashboard_initial_row_cap,
        test_dashboard_load_more_increases_rows,
        test_dashboard_filters_reset_pagination,
        test_dashboard_filter_pills_present,
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
