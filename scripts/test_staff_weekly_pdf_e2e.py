#!/usr/bin/env python3
"""E2E tests — Staff Portal Weekly PDF (/staff/weekly/schedule.pdf).

The PDF must show exactly what the Weekly screen shows: same jobs, same
Monday→Sunday days, same start-time order, same per-staff times and the same
Paid Hours. Each test uses its own far-future week so leftover local bookings
never interfere.
"""

import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import pymupdf  # noqa: E402

import database as db  # noqa: E402
import extra_charges  # noqa: E402
from app import app  # noqa: E402
from integrations import weekly_schedule_pdf  # noqa: E402
from staff_portal import (  # noqa: E402
    RANGE_WEEK,
    build_staff_portal,
    build_staff_weekly_pdf_schedule,
)

DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
_n = 0
_used_mondays = set()


def _unique(prefix):
    global _n
    _n += 1
    return "{0}-{1}-{2}".format(prefix, os.getpid(), _n)


def _ensure_crew():
    db.init_db()
    existing = {row["name"] for row in db.list_crew_members(active_only=False)}
    for name in ("Yasu", "Tom", "Ken"):
        if name not in existing:
            db.create_crew_member(name, role="Driver", active=1)


def _crew_id(name):
    for row in db.list_crew_members(active_only=False):
        if str(row.get("name") or "").strip() == name:
            return int(row["id"])
    raise AssertionError("crew member missing: " + name)


def _isolated_monday(year_lo=2400, year_hi=2900):
    while True:
        day = date(random.randint(year_lo, year_hi), 1, 1) + timedelta(
            days=random.randint(0, 360)
        )
        monday = day - timedelta(days=day.weekday())
        if monday not in _used_mondays and not db.list_between_dates(
            monday.isoformat(), (monday + timedelta(days=6)).isoformat()
        ):
            _used_mondays.add(monday)
            return monday


def _job(customer, move_date, *, crew="Yasu", start="08:00", finish="12:00",
         status="Confirmed", callout_minutes=30, pickup="12 Test St, Cannington WA 6107",
         dropoff="8 River Ave, Como WA 6152", phone="0412000456", notes=""):
    _ensure_crew()
    return db.create_booking(
        customer,
        phone,
        "weekly-pdf@example.com",
        pickup,
        dropoff,
        move_date.isoformat() if hasattr(move_date, "isoformat") else move_date,
        2,
        notes,
        start_time=start,
        finish_time=finish,
        duration_hours="",
        status=status,
        crew=crew,
        hourly_rate=180.0,
        callout_fee=round(180.0 * callout_minutes / 60.0, 2),
        callout_minutes=callout_minutes,
    )


def _add_break(booking_id, minutes):
    items = db.list_extra_charges(booking_id)
    items.append(
        {
            "description": extra_charges.break_deduction_description(minutes),
            "quantity": 1,
            "unit_price": extra_charges.break_deduction_unit_price(180.0, minutes),
        }
    )
    db.replace_extra_charges(booking_id, items)


def _pdf(staff_id, today):
    schedule = build_staff_weekly_pdf_schedule(staff_id, 0, today)
    data = weekly_schedule_pdf.render_staff_weekly_schedule_pdf(schedule)
    return schedule, data, pymupdf.open(stream=data, filetype="pdf")


def _text(doc):
    """Page text with en dashes folded to '-' (Helvetica fallback) and lines joined."""
    raw = "\n".join(page.get_text() for page in doc)
    return " ".join(raw.replace("\u2013", "-").split())


def _col_bounds(page):
    width = page.rect.width
    margin = weekly_schedule_pdf.MARGIN
    col = weekly_schedule_pdf.COL_W
    return [(margin + i * col, margin + (i + 1) * col) for i in range(7)], width


def _find(doc, needle):
    """[(page_no, column_index, y)] for every hit of needle."""
    hits = []
    for page_no, page in enumerate(doc):
        cols, _ = _col_bounds(page)
        for rect in page.search_for(needle):
            cx = (rect.x0 + rect.x1) / 2
            for idx, (left, right) in enumerate(cols):
                if left <= cx < right:
                    hits.append((page_no, idx, rect.y0))
    return hits


def _screen_day_ids(staff_id, today):
    portal = build_staff_portal(range_key=RANGE_WEEK, today=today, view_staff_id=staff_id)
    days = (
        portal["owner_weekly"]["days"] if portal["is_all_staff"] else portal["week_days"]
    )
    return portal, {day["date_iso"]: [job["id"] for job in day["jobs"]] for day in days}


def _assert_matches_screen(staff_id, today):
    portal, screen = _screen_day_ids(staff_id, today)
    schedule = build_staff_weekly_pdf_schedule(staff_id, 0, today)
    pdf = {day["date_iso"]: [job["id"] for job in day["jobs"]] for day in schedule["days"]}
    assert [day["heading"].split()[0] for day in schedule["days"]] == list(DAYS)
    assert screen == pdf, (screen, pdf)
    return portal, schedule


# --- Cases ---------------------------------------------------------------------


def test_case_a_single_job_shows_in_pdf():
    monday = _isolated_monday()
    name = _unique("CaseA Single")
    _job(name, monday + timedelta(days=1), crew="Yasu")
    for staff in ("all", _crew_id("Yasu")):
        _assert_matches_screen(staff, monday)
        schedule, data, doc = _pdf(staff, monday)
        assert doc.page_count == 1
        hits = _find(doc, name)
        assert hits and hits[0][1] == 1, hits  # Tuesday column
        assert "NO JOBS" in _text(doc)
    return True


def test_case_b_three_jobs_sorted_by_real_time():
    monday = _isolated_monday()
    wed = monday + timedelta(days=2)
    late = _unique("CaseB Late")
    early = _unique("CaseB Early")
    mid = _unique("CaseB Mid")
    noon = _unique("CaseB Noon")
    tbc = _unique("CaseB TBC")
    # Inserted out of order; "1:00 PM" < "12:00 PM" < "8:00 AM" as strings.
    _job(late, wed, start="13:00", finish="15:00", crew="Yasu,Ken")
    _job(tbc, wed, start="", finish="", crew="Yasu,Ken")
    _job(noon, wed, start="12:00", finish="13:00", crew="Yasu,Ken")
    _job(early, wed, start="08:00", finish="09:00", crew="Yasu,Ken")
    _job(mid, wed, start="09:30", finish="11:00", crew="Yasu,Ken")
    for staff in ("all", _crew_id("Yasu"), _crew_id("Ken")):
        _assert_matches_screen(staff, monday)
        _schedule, _data, doc = _pdf(staff, monday)
        ys = []
        for name in (early, mid, noon, late, tbc):
            hits = _find(doc, name)
            assert len(hits) == 1 and hits[0][1] == 2, (name, hits)
            ys.append(hits[0][2])
        assert ys == sorted(ys), ys
        assert "TIME TBC" in _text(doc)
    return True


def test_case_c_sunday_and_monday_jobs_show():
    monday = _isolated_monday()
    sun_job = _unique("CaseC Sunday")
    mon_job = _unique("CaseC Monday")
    _job(sun_job, monday + timedelta(days=6), crew="Tom", status="Completed")
    _job(mon_job, monday, crew="Tom", status="Paid")
    for staff in ("all", _crew_id("Tom")):
        _assert_matches_screen(staff, monday + timedelta(days=3))
        _schedule, _data, doc = _pdf(staff, monday + timedelta(days=3))
        assert _find(doc, sun_job)[0][1] == 6
        assert _find(doc, mon_job)[0][1] == 0
        text = _text(doc)
        assert text.index("MONDAY") < text.index("SUNDAY")
    return True


def test_case_d_individual_override_uses_personal_time():
    monday = _isolated_monday()
    tue = monday + timedelta(days=1)
    name = _unique("CaseD Override")
    bid = _job(name, tue, crew="Yasu,Ken", start="08:00", finish="12:00", status="Completed")
    db.upsert_booking_crew_hours(bid, _crew_id("Ken"), start_time="10:00", finish_time="14:00")

    _s, _d, ken = _pdf(_crew_id("Ken"), monday)
    _s, _d, yasu = _pdf(_crew_id("Yasu"), monday)
    _s, _d, everyone = _pdf("all", monday)
    assert "10:00 AM - 2:00 PM" in _text(ken)
    assert "8:00 AM - 12:00 PM" not in _text(ken)
    assert "8:00 AM - 12:00 PM" in _text(yasu)  # Ken's override is not shared
    assert "10:00 AM" not in _text(yasu)
    assert "8:00 AM - 12:00 PM" in _text(everyone)  # All Staff = booking time
    assert "10:00 AM" not in _text(everyone)
    for doc in (ken, yasu, everyone):
        assert "Crew: Yasu / Ken" in _text(doc)  # crew lists everyone
    return True


def test_case_e_break_reduces_paid_hours():
    monday = _isolated_monday()
    name = _unique("CaseE Break")
    bid = _job(name, monday, crew="Yasu", status="Completed", callout_minutes=30)
    _add_break(bid, 30)
    portal, _ = _assert_matches_screen(_crew_id("Yasu"), monday)
    screen_job = [j for d in portal["week_days"] for j in d["jobs"] if j["id"] == bid][0]
    assert screen_job["paid_hours_display"] == "4hr"  # 4 − 0.5 + 0.5
    _s, _d, doc = _pdf(_crew_id("Yasu"), monday)
    text = " ".join(_text(doc).split())
    assert "Actual 4hr" in text
    assert "Break -0.5hr" in text
    assert "Paid 4hr" in text
    assert "Daily Paid: 4hr" in text
    assert "Weekly Paid Hours: 4hr" in text
    return True


def test_case_f_callout_45_minutes():
    monday = _isolated_monday()
    name = _unique("CaseF Callout45")
    _job(name, monday, crew="Yasu", status="Completed", callout_minutes=45)
    _s, _d, doc = _pdf(_crew_id("Yasu"), monday)
    text = " ".join(_text(doc).split())
    assert "Call Out + 0.75hr (45 min)" in text
    assert "Paid 4.75hr" in text
    _s, _d, everyone = _pdf("all", monday)
    assert "Paid 4.75hr" in " ".join(_text(everyone).split())
    return True


def test_case_g_extra_pay_adds_to_paid_hours():
    monday = _isolated_monday()
    name = _unique("CaseG Extra")
    bid = _job(name, monday, crew="Yasu,Ken", status="Completed", callout_minutes=30)
    db.upsert_booking_crew_hours(bid, _crew_id("Yasu"), extra_paid_minutes=30)
    _s, _d, yasu = _pdf(_crew_id("Yasu"), monday)
    _s, _d, ken = _pdf(_crew_id("Ken"), monday)
    _s, _d, everyone = _pdf("all", monday)
    yasu_text = " ".join(_text(yasu).split())
    assert "Extra Pay +0.5hr" in yasu_text
    assert "Paid 5hr" in yasu_text  # 4 + 0.5 call out + 0.5 extra
    ken_text = " ".join(_text(ken).split())
    assert "Extra Pay" not in ken_text
    assert "Paid 4.5hr" in ken_text
    assert "Extra Pay" not in _text(everyone)
    return True


def test_case_h_all_staff_one_card_per_booking():
    monday = _isolated_monday()
    name = _unique("CaseH Shared")
    _job(name, monday + timedelta(days=3), crew="Yasu,Ken,Tom")
    schedule, _d, doc = _pdf("all", monday)
    assert len(_find(doc, name)) == 1
    ids = [job["id"] for day in schedule["days"] for job in day["jobs"]]
    assert len(ids) == len(set(ids))
    assert "Crew: Yasu / Ken / Tom" in _text(doc)
    for staff in ("Yasu", "Ken", "Tom"):
        _s, _d, own = _pdf(_crew_id(staff), monday)
        assert len(_find(own, name)) == 1
    return True


def test_case_i_long_text_and_japanese_stay_inside_column():
    monday = _isolated_monday()
    long_name = _unique("CaseI " + "Very-Long-Customer-Name-Without-Spaces-" * 3)
    jp_name = _unique("山田太郎様 引越し")
    _job(
        long_name,
        monday + timedelta(days=4),
        crew="Yasu,Ken,Tom",
        pickup="Unit 1234/567 The-Extremely-Long-Street-Name-Boulevard-Without-Breaks, "
        "Mount Lawley Western Australia 6050",
        dropoff="https://example.com/a/very/long/path/that/never/has/a/space/in/it/at/all",
        notes="Grand piano + 3 fridges. " * 12,
    )
    _job(jp_name, monday + timedelta(days=4), crew="Yasu", start="13:00", finish="15:00",
         pickup="東京都新宿区西新宿二丁目八番一号 長い住所のテストです", phone="+61 400 000 000")
    for staff in ("all", _crew_id("Yasu")):
        _assert_matches_screen(staff, monday)
        _s, _d, doc = _pdf(staff, monday)
        assert doc.page_count == 1
        text = _text(doc)
        assert "山田太郎様" in text
        assert "東京都新宿区" in text.replace(" ", "")
        page = doc[0]
        cols, width = _col_bounds(page)
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if not span["text"].strip():
                        continue
                    x0, _y0, x1, _y1 = span["bbox"]
                    cx = (x0 + x1) / 2
                    col = [c for c in cols if c[0] <= cx < c[1]]
                    if not col or _y0 < 60:
                        continue  # page header
                    left, right = col[0]
                    assert x0 >= left - 0.5 and x1 <= right + 0.5, (span["text"], span["bbox"], col)
    return True


def test_month_and_year_crossing_week():
    while True:
        year = random.randint(2400, 2900)
        jan1 = date(year + 1, 1, 1)
        if jan1.weekday() not in (0,):  # week must contain both Dec and Jan days
            monday = jan1 - timedelta(days=jan1.weekday())
            if monday not in _used_mondays and not db.list_between_dates(
                monday.isoformat(), (monday + timedelta(days=6)).isoformat()
            ):
                _used_mondays.add(monday)
                break
    dec31 = _unique("YearEnd Dec31")
    jan01 = _unique("YearEnd Jan01")
    _job(dec31, jan1 - timedelta(days=1), crew="Yasu", status="Invoiced")
    _job(jan01, jan1, crew="Yasu", status="Confirmed")
    for staff in ("all", _crew_id("Yasu")):
        _assert_matches_screen(staff, monday)
        schedule, _d, doc = _pdf(staff, monday)
        assert schedule["week_start"] == monday.isoformat()
        assert _find(doc, dec31)[0][1] == (jan1 - timedelta(days=1)).weekday()
        assert _find(doc, jan01)[0][1] == jan1.weekday()
        text = _text(doc)
        assert "DEC" in text and "JAN" in text
        assert "INVOICED" in text and "CONFIRMED" in text
    return True


def test_statuses_shown_and_cancelled_rule_matches_screen():
    monday = _isolated_monday()
    names = {}
    for offset, status in enumerate(("Confirmed", "Completed", "Invoiced", "Paid", "Cancelled")):
        names[status] = _unique("Status " + status)
        _job(names[status], monday + timedelta(days=offset), crew="Ken", status=status)
    portal, _schedule = _assert_matches_screen(_crew_id("Ken"), monday)
    _p, _s2 = _assert_matches_screen("all", monday)
    _s, _d, ken = _pdf(_crew_id("Ken"), monday)
    for status in ("Confirmed", "Completed", "Invoiced", "Paid"):
        assert _find(ken, names[status]), status
        assert status.upper() in _text(ken)
    assert not _find(ken, names["Cancelled"])  # individual Weekly hides cancelled
    _s, _d, everyone = _pdf("all", monday)
    assert _find(everyone, names["Cancelled"])  # All Staff Weekly shows it
    return True


def test_paid_hours_match_screen_exactly():
    monday = _isolated_monday()
    a = _job(_unique("Paid A"), monday, crew="Yasu,Ken", status="Completed", callout_minutes=45)
    _add_break(a, 15)
    b = _job(_unique("Paid B"), monday + timedelta(days=2), crew="Yasu", status="Completed",
             start="07:30", finish="16:15", callout_minutes=60)
    db.upsert_booking_crew_hours(b, _crew_id("Yasu"), actual_start_time="07:45",
                                 actual_finish_time="15:30", extra_paid_minutes=30)
    for staff in (_crew_id("Yasu"), _crew_id("Ken"), "all"):
        portal, schedule = _assert_matches_screen(staff, monday)
        days = portal["owner_weekly"]["days"] if portal["is_all_staff"] else portal["week_days"]
        screen = {j["id"]: j["paid_hours_display"] for d in days for j in d["jobs"]}
        pdf = {j["id"]: j["paid_hours_display"] for d in schedule["days"] for j in d["jobs"]}
        assert screen == pdf, (screen, pdf)
        if not portal["is_all_staff"]:
            assert schedule["weekly_paid_display"] == portal["weekly_worked"]["paid_display"]
            for sd, pd in zip(portal["week_days"], schedule["days"]):
                assert sd["paid_display"] == pd["paid_display"]
    return True


def test_busy_week_keeps_every_job():
    monday = _isolated_monday()
    names = []
    for day in range(7):
        for slot in range(4):
            name = _unique("Busy d{0}s{1}".format(day, slot))
            names.append(name)
            _job(name, monday + timedelta(days=day), crew="Yasu,Ken,Tom",
                 start="{0:02d}:00".format(7 + slot * 3), finish="{0:02d}:30".format(9 + slot * 3),
                 pickup="100 Some Long Street Name, Suburbia WA 6000",
                 dropoff="200 Another Long Road, Elsewhere WA 6100")
    schedule, _d, doc = _pdf("all", monday)
    missing = [n for n in names if not _find(doc, n)]
    assert not missing, missing
    assert schedule["_fit_scale"] >= weekly_schedule_pdf.STAFF_SCALES[-1]
    print("  busy week (28 jobs): pages={0} scale={1}".format(doc.page_count, schedule["_fit_scale"]))
    return True


def test_route_and_no_pdf_on_screen_view():
    monday = _isolated_monday()
    _job(_unique("Route Job"), monday, crew="Yasu")
    client = app.test_client()
    calls = []
    original = weekly_schedule_pdf.render_staff_weekly_schedule_pdf

    def spy(schedule):
        calls.append(schedule.get("mode"))
        return original(schedule)

    weekly_schedule_pdf.render_staff_weekly_schedule_pdf = spy
    try:
        for staff in ("all", str(_crew_id("Yasu"))):
            page = client.get("/staff?staff_id={0}&range=week".format(staff))
            assert page.status_code == 200
            assert "Download Weekly PDF" in page.get_data(as_text=True)
        assert calls == [], calls  # the screen never renders a PDF
        resp = client.get("/staff/weekly/schedule.pdf?staff_id=all&week=0")
        assert resp.status_code == 200 and resp.mimetype == "application/pdf"
        assert "weekly-schedule-" in resp.headers["Content-Disposition"]
        resp = client.get("/staff/weekly/schedule.pdf?staff_id={0}&week=-1".format(_crew_id("Yasu")))
        assert resp.status_code == 200
        assert "staff-weekly-{0}-".format(_crew_id("Yasu")) in resp.headers["Content-Disposition"]
        assert calls == ["all", "individual"], calls
    finally:
        weekly_schedule_pdf.render_staff_weekly_schedule_pdf = original
    return True


def main():
    tests = [
        test_case_a_single_job_shows_in_pdf,
        test_case_b_three_jobs_sorted_by_real_time,
        test_case_c_sunday_and_monday_jobs_show,
        test_case_d_individual_override_uses_personal_time,
        test_case_e_break_reduces_paid_hours,
        test_case_f_callout_45_minutes,
        test_case_g_extra_pay_adds_to_paid_hours,
        test_case_h_all_staff_one_card_per_booking,
        test_case_i_long_text_and_japanese_stay_inside_column,
        test_month_and_year_crossing_week,
        test_statuses_shown_and_cancelled_rule_matches_screen,
        test_paid_hours_match_screen_exactly,
        test_busy_week_keeps_every_job,
        test_route_and_no_pdf_on_screen_view,
    ]
    passed = 0
    for test in tests:
        try:
            test()
            print("PASS: {0}".format(test.__name__))
            passed += 1
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            print("FAIL: {0}: {1!r}".format(test.__name__, exc))
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
