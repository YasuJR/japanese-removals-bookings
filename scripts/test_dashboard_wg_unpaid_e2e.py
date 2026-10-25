#!/usr/bin/env python3
"""E2E tests — Dashboard REGULAR UNPAID vs WG OUTDOOR LIFE sections."""

import os
import re
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import invoice
from app import app
from dashboard_data import (
    SECTION_PAID,
    SECTION_TODAY_UPCOMING,
    SECTION_UNPAID,
    SECTION_WG_UNPAID,
    dashboard_section_group,
    dashboard_section_layout,
    dashboard_unpaid_summaries,
    perth_today,
)
from wg_jobs import is_wg_outdoor_life_name

_counter = 0


def _login_client():
    global _counter
    _counter += 1
    db.init_db()
    label = "dash-wg-{0}-{1}".format(os.getpid(), _counter)
    uid = db.create_staff_user(label, auth.hash_password("test"), "WG Test")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _tag():
    global _counter
    _counter += 1
    return "{0}-{1}".format(os.getpid(), _counter)


def _create_job(name, move_date, payment_status="Unpaid", status="Completed",
                start_time="09:00", hours="3"):
    db.init_db()
    return db.create_booking(
        name,
        "0412555888",
        "wg-test-{0}@example.com".format(os.getpid()),
        "1 WG Test St, Perth WA",
        "2 WG Test Ave, Fremantle WA",
        move_date,
        2,
        "dashboard WG test",
        start_time=start_time,
        duration_hours=hours,
        hourly_rate=100.0,
        payment_status=payment_status,
        status=status,
    )


def _customer_names(html):
    return [
        name.strip()
        for name in re.findall(
            r'class="customer-link"[^>]*>\s*<strong>([^<]+)</strong>', html
        )
    ]


def _dashboard_html(client):
    return client.get("/dashboard?filter=all&jobs_limit=100000").get_data(as_text=True)


def _divider_meta(html, kind):
    match = re.search(
        r'dashboard-{0}-divider-row.*?dashboard-section-divider-meta">([^<]+)<'.format(kind),
        html,
        re.S,
    )
    return match.group(1).strip() if match else None


def test_wg_name_matching_handles_variants_without_false_positives():
    for name in [
        "WG",
        "wg",
        " WG Outdoor Life ",
        "WG Outdoor life",
        "WG  outdoor   LIFE",
        "WG-Outdoor Life",
        "WG Outdoor Life (Joondalup)",
        "WG 2nd truck",
        "ＷＧ Outdoor Life",
        "Delivery for WG Outdoor Life",
    ]:
        assert is_wg_outdoor_life_name(name), name
    for name in [
        "",
        None,
        "Wgrace",
        "WGM Logistics",
        "Dwg Studio",
        "Greg Wg Smith",
        "Outdoor Life Co",
        "Lisa Booth",
        "Twg Outdoor Lifestyle",
    ]:
        assert not is_wg_outdoor_life_name(name), name
    return True


def test_section_group_rules():
    today = "2026-10-25"
    past = "2026-10-20"
    assert dashboard_section_group({"customer_name": "WG", "payment_status": "Unpaid", "move_date": past}, today) == SECTION_WG_UNPAID
    assert dashboard_section_group({"customer_name": "WG Outdoor Life", "payment_status": "Part Paid", "move_date": past}, today) == SECTION_WG_UNPAID
    assert dashboard_section_group({"customer_name": "WG", "payment_status": "Paid", "move_date": past}, today) == SECTION_PAID
    # Upcoming WG work stays in TODAY & UPCOMING until the job date passes.
    assert dashboard_section_group({"customer_name": "WG", "payment_status": "Unpaid", "move_date": today}, today) == SECTION_TODAY_UPCOMING
    assert dashboard_section_group({"customer_name": "Lisa", "payment_status": "Unpaid", "move_date": past}, today) == SECTION_UNPAID
    layout = dashboard_section_layout(
        [
            {"customer_name": "A", "payment_status": "Unpaid", "move_date": today},
            {"customer_name": "B", "payment_status": "Unpaid", "move_date": past},
            {"customer_name": "WG", "payment_status": "Unpaid", "move_date": "2026-01-01"},
            {"customer_name": "WG", "payment_status": "Unpaid", "move_date": past},
            {"customer_name": "C", "payment_status": "Paid", "move_date": past},
        ],
        today,
    )
    assert layout == {"upcoming": 0, "unpaid": 1, "wg": 2, "wg_last": 3, "paid": 4}, layout
    return True


def test_regular_and_wg_sections_are_separate_and_not_duplicated():
    tag = _tag()
    today = perth_today()
    regular_old = "Regular Old {0}".format(tag)
    regular_new = "Regular New {0}".format(tag)
    wg_very_old = "WG Outdoor Life Very Old {0}".format(tag)
    wg_recent = "WG Recent {0}".format(tag)
    wg_paid = "WG Outdoor life Paid {0}".format(tag)
    wg_upcoming = "WG Upcoming {0}".format(tag)
    lookalike = "Wgrace Lookalike {0}".format(tag)
    _create_job(regular_new, (today - timedelta(days=2)).isoformat())
    _create_job(regular_old, (today - timedelta(days=30)).isoformat())
    _create_job(lookalike, (today - timedelta(days=10)).isoformat())
    _create_job(wg_recent, (today - timedelta(days=3)).isoformat())
    # Over a year old: must still be listed while unpaid.
    _create_job(wg_very_old, (today - timedelta(days=400)).isoformat())
    _create_job(wg_paid, (today - timedelta(days=5)).isoformat(), payment_status="Paid")
    _create_job(wg_upcoming, (today + timedelta(days=3)).isoformat(), status="Confirmed")

    html = _dashboard_html(_login_client())
    upcoming_h = html.find("dashboard-upcoming-divider-row")
    regular_h = html.find("dashboard-unpaid-divider-row")
    wg_h = html.find("dashboard-wg-divider-row")
    wg_end = html.find("dashboard-wg-end-divider-row")
    paid_h = html.find("dashboard-paid-divider-row")
    assert -1 not in (upcoming_h, regular_h, wg_h, wg_end, paid_h)
    assert upcoming_h < regular_h < wg_h < wg_end < paid_h
    assert "REGULAR UNPAID" in html and "WG OUTDOOR LIFE" in html

    assert upcoming_h < html.find(wg_upcoming) < regular_h
    for name in (regular_old, regular_new, lookalike):
        assert regular_h < html.find(name) < wg_h, name
    for name in (wg_very_old, wg_recent):
        assert wg_h < html.find(name) < wg_end, name
    assert paid_h < html.find(wg_paid)

    names = _customer_names(html)
    for name in (regular_old, regular_new, lookalike, wg_very_old, wg_recent, wg_paid, wg_upcoming):
        assert names.count(name) == 1, (name, names.count(name))

    # Oldest unpaid first within each section.
    mine = [n for n in names if n.endswith(tag)]
    assert mine.index(regular_old) < mine.index(lookalike) < mine.index(regular_new), mine
    assert mine.index(wg_very_old) < mine.index(wg_recent), mine
    return True


def test_section_counts_and_totals_are_separate():
    tag = _tag()
    today = perth_today()
    regular_ids = [
        _create_job("Count Regular {0} {1}".format(i, tag), (today - timedelta(days=4 + i)).isoformat(), hours=str(2 + i))
        for i in range(2)
    ]
    wg_ids = [
        _create_job("WG Outdoor Life Count {0} {1}".format(i, tag), (today - timedelta(days=60 * (i + 1))).isoformat(), hours=str(3 + i))
        for i in range(3)
    ]
    rows = [dict(db.get_booking(i)) for i in regular_ids + wg_ids]
    summary = dashboard_unpaid_summaries(rows, today.isoformat())
    assert summary["regular"]["count"] == 2, summary
    assert summary["wg"]["count"] == 3, summary
    expected_regular = round(sum(invoice.calculate_invoice_totals(dict(db.get_booking(i)))["total"] for i in regular_ids), 2)
    expected_wg = round(sum(invoice.calculate_invoice_totals(dict(db.get_booking(i)))["total"] for i in wg_ids), 2)
    assert summary["regular"]["total"] == expected_regular > 0, summary
    assert summary["wg"]["total"] == expected_wg > 0, summary

    # The rendered dividers show the full-list summary for each group.
    full = dashboard_unpaid_summaries(db.list_for_dashboard("all", today.isoformat()), today.isoformat())
    html = _dashboard_html(_login_client())
    def meta(s):
        return "{0} job{1} · Total: {2}".format(s["count"], "" if s["count"] == 1 else "s", invoice.format_aud(s["total"]))
    assert _divider_meta(html, "unpaid") == meta(full["regular"]), (_divider_meta(html, "unpaid"), full)
    assert _divider_meta(html, "wg") == meta(full["wg"]), (_divider_meta(html, "wg"), full)
    assert full["wg"]["count"] >= 3 and full["regular"]["count"] >= 2
    return True


def test_wg_job_marked_paid_leaves_wg_section():
    tag = _tag()
    today = perth_today()
    name = "WG Pay Me {0}".format(tag)
    booking_id = _create_job(name, (today - timedelta(days=90)).isoformat())
    client = _login_client()
    html = _dashboard_html(client)
    assert html.find("dashboard-wg-divider-row") < html.find(name) < html.find("dashboard-wg-end-divider-row")
    before = dashboard_unpaid_summaries(db.list_for_dashboard("all", today.isoformat()), today.isoformat())

    resp = client.post("/bookings/{0}/payment".format(booking_id), json={"payment_status": "Paid"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert dict(db.get_booking(booking_id))["payment_status"] == "Paid"

    html = _dashboard_html(client)
    paid_h = html.find("dashboard-paid-divider-row")
    assert paid_h != -1 and paid_h < html.find(name)
    wg_end = html.find("dashboard-wg-end-divider-row")
    if wg_end != -1:
        assert wg_end < html.find(name)
    after = dashboard_unpaid_summaries(db.list_for_dashboard("all", today.isoformat()), today.isoformat())
    assert after["wg"]["count"] == before["wg"]["count"] - 1
    assert after["regular"] == before["regular"]
    return True


def test_regular_header_shows_zero_when_only_wg_is_unpaid():
    from flask import render_template_string

    jobs = [{"id": 1, "customer_name": "WG", "payment_status": "Unpaid", "move_date": "2026-01-01",
             "status": "Completed", "num_movers": 2}]
    with app.test_request_context("/dashboard"):
        source = (ROOT / "templates" / "dashboard.html").read_text()
        start = source.index("{% for b in jobs %}")
        end = source.index("{% endfor %}", source.index("payment_wg_last_index is not none and loop.index0")) + len("{% endfor %}")
        snippet = source[start:end]
        html = render_template_string(
            snippet,
            jobs=jobs,
            upcoming_divider_index=None,
            payment_unpaid_index=None,
            payment_wg_index=0,
            payment_wg_last_index=0,
            payment_paid_index=None,
            unpaid_summaries={"regular": {"count": 0, "total": 0.0}, "wg": {"count": 1, "total": 300.0}},
        )
    assert html.find("dashboard-unpaid-divider-row") < html.find("dashboard-wg-divider-row") < html.find("dashboard-wg-end-divider-row")
    assert "0 jobs · Total: $0.00" in html
    assert "1 job · Total: $300.00" in html
    return True


def test_css_styles_wg_divider():
    css = (ROOT / "static" / "style.css").read_text()
    assert ".dashboard-wg-divider-row .dashboard-section-divider::before" in css
    assert ".dashboard-wg-end-divider-row" in css
    assert ".dashboard-section-divider-meta" in css
    assert ".dashboard-section-divider-meta" in (ROOT / "static" / "mobile.css").read_text()
    return True


def main():
    tests = [
        test_wg_name_matching_handles_variants_without_false_positives,
        test_section_group_rules,
        test_regular_and_wg_sections_are_separate_and_not_duplicated,
        test_section_counts_and_totals_are_separate,
        test_wg_job_marked_paid_leaves_wg_section,
        test_regular_header_shows_zero_when_only_wg_is_unpaid,
        test_css_styles_wg_divider,
    ]
    passed = 0
    for test in tests:
        try:
            if test():
                print("PASS:", test.__name__)
                passed += 1
            else:
                print("FAIL:", test.__name__)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            print("FAIL:", test.__name__, "—", repr(exc))
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
