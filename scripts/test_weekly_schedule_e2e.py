#!/usr/bin/env python3
"""E2E tests — Weekly Schedule page and A4 landscape PDF."""

import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import weekly_schedule_data
from app import app
from integrations import weekly_schedule_pdf

SCREENSHOT_DIR = Path("/opt/cursor/artifacts/screenshots")
PDF_DIR = Path("/opt/cursor/artifacts")
_test_user_counter = 0


def _page_count(pdf_bytes: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf_bytes))


def _pdf_plain_text(pdf_bytes: bytes) -> str:
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def _save_pdf_preview(pdf_bytes: bytes, filename: str) -> Path:
    import pymupdf

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = PDF_DIR / filename
    pdf_path.write_bytes(pdf_bytes)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    preview = SCREENSHOT_DIR / (Path(filename).stem + ".png")
    pix.save(str(preview))
    return preview


def _pdf_mediabox(pdf_bytes: bytes):
    match = re.search(
        rb"/MediaBox\s*\[\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s*\]",
        pdf_bytes,
    )
    assert match, "MediaBox missing from PDF"
    return tuple(float(match.group(i)) for i in range(1, 5))


def _login_client():
    global _test_user_counter
    _test_user_counter += 1
    db.init_db()
    uid = db.create_staff_user(
        "weekly-schedule-{0}-{1}".format(os.getpid(), _test_user_counter),
        auth.hash_password("test"),
        "Weekly Schedule Test",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client


def _create_booking(
    customer,
    move_date,
    start_time,
    finish_time,
    crew="Katsu,Keiichi",
    notes="WEEKLY_NOTES_SECRET",
    num_movers=2,
    pickup="2/280 Robinson Avenue, Cloverdale",
    delivery="5a Brown Street, Middle Swan",
):
    return db.create_booking(
        customer,
        "0412000456",
        "weekly-schedule@example.com",
        pickup,
        delivery,
        move_date,
        num_movers,
        notes,
        start_time=start_time,
        finish_time=finish_time,
        duration_hours="4",
        status="Confirmed",
        crew=crew,
    )


def _week_starting(monday: date):
    assert monday.weekday() == 0, monday
    return [monday + timedelta(days=offset) for offset in range(7)]


def test_monday_to_sunday_order_and_empty_days():
    monday = date(2098, 9, 8)
    assert monday.weekday() == 0
    days = _week_starting(monday)
    prefix = "Ord{0}-".format(os.getpid())
    _create_booking(prefix + "Early Liam", days[0].isoformat(), "11:00", "13:00")
    _create_booking(prefix + "Late Liam", days[0].isoformat(), "08:00", "10:00")
    _create_booking(prefix + "Wednesday Only", days[2].isoformat(), "09:00", "12:00")
    _create_booking(prefix + "Saturday Job", days[5].isoformat(), "08:15", "10:45")
    _create_booking(prefix + "Sunday Job", days[6].isoformat(), "08:00", "10:15")

    weekly = weekly_schedule_data.build_weekly_schedule(
        days[3].isoformat(),
        reference=date(2098, 8, 20),
    )
    assert weekly["week_start"] == monday.isoformat()
    assert weekly["week_end"] == days[6].isoformat()
    assert weekly["range_heading"] == "8 SEP – 14 SEP 2098"
    names = [day["weekday"] for day in weekly["days"]]
    assert names == [
        "MONDAY",
        "TUESDAY",
        "WEDNESDAY",
        "THURSDAY",
        "FRIDAY",
        "SATURDAY",
        "SUNDAY",
    ]
    monday_jobs = [
        job for job in weekly["days"][0]["jobs"] if job["customer_name"].startswith(prefix)
    ]
    assert [job["customer_name"] for job in monday_jobs] == [
        prefix + "Late Liam",
        prefix + "Early Liam",
    ]
    assert monday_jobs[0]["duration_label"] == "2hr"
    assert monday_jobs[0]["time_range"] == "8:00 AM – 10:00 AM"
    assert weekly["days"][5]["jobs"][-1]["duration_label"] == "2.5hr"
    assert weekly["days"][6]["jobs"][-1]["duration_label"] == "2.25hr"
    for index in (1, 3, 4):
        own = [job for job in weekly["days"][index]["jobs"] if job["customer_name"].startswith(prefix)]
        assert own == []
    assert weekly["days"][5]["is_weekend"] is True
    assert weekly["days"][6]["is_weekend"] is True
    return True


def test_month_spanning_week_heading():
    monday = date(2097, 4, 29)
    assert monday.weekday() == 0
    weekly = weekly_schedule_data.build_weekly_schedule(
        "2097-05-02",
        reference=date(2097, 5, 2),
    )
    assert weekly["week_start"] == "2097-04-29"
    assert weekly["week_end"] == "2097-05-05"
    assert weekly["range_heading"] == "29 APR – 5 MAY 2097"
    assert weekly["days"][0]["heading"] == "MONDAY 29 APR"
    assert weekly["days"][2]["heading"] == "WEDNESDAY 1 MAY"
    assert weekly["days"][6]["heading"] == "SUNDAY 5 MAY"
    return True


def test_cross_year_week_heading():
    weekly = weekly_schedule_data.build_weekly_schedule("2025-12-31")
    assert weekly["week_start"] == "2025-12-29"
    assert weekly["week_end"] == "2026-01-04"
    assert weekly["range_heading"] == "29 DEC 2025 – 4 JAN 2026"
    return True


def test_weekly_page_nav_and_fields():
    monday = date(2097, 6, 3)
    assert monday.weekday() == 0
    _create_booking(
        "Liam Warn",
        monday.isoformat(),
        "08:00",
        "10:00",
        notes="WEEKLY_NOTES_SECRET do not print",
    )
    client = _login_client()
    html = client.get("/calendar/weekly/{0}".format(monday.isoformat())).get_data(
        as_text=True
    )
    assert "WEEKLY SCHEDULE" in html
    assert "3 JUN – 9 JUN 2097" in html
    assert "← Previous Week" in html
    assert "This Week" in html
    assert "Next Week →" in html
    assert "Download Weekly PDF" in html
    assert "MONDAY 3 JUN" in html
    assert "SUNDAY 9 JUN" in html
    assert "Liam Warn" in html
    assert "8:00 AM – 10:00 AM" in html
    assert "2hr" in html
    assert "Crew:" in html
    assert "Katsu / Keiichi" in html
    assert "Pickup:" in html
    assert "Delivery:" in html
    assert "Phone:" in html
    assert "Status:" in html
    assert "CONFIRMED" in html
    assert "NO JOBS" in html
    assert "WEEKLY_NOTES_SECRET" not in html
    assert "/calendar/weekly/{0}/schedule.pdf".format(monday.isoformat()) in html
    assert "/calendar/weekly/{0}".format((monday - timedelta(days=7)).isoformat()) in html
    assert "/calendar/weekly/{0}".format((monday + timedelta(days=7)).isoformat()) in html
    return True


def test_weekly_pdf_one_landscape_page_even_when_busy():
    monday = date(2101, 1, 1) + timedelta(days=(os.getpid() % 40) * 7)
    monday = monday - timedelta(days=monday.weekday())
    days = _week_starting(monday)
    prefix = "WS{0}-".format(os.getpid())
    _create_booking(prefix + "Solo Tuesday", days[1].isoformat(), "09:00", "11:00")
    for offset in (0, 2, 5, 6):
        for index in range(4):
            start_hour = 7 + index * 2
            _create_booking(
                "{0}Busy {1}-{2}".format(prefix, days[offset].strftime("%a"), index + 1),
                days[offset].isoformat(),
                "{0:02d}:00".format(start_hour),
                "{0:02d}:00".format(start_hour + 2),
                pickup="{0}/280 Robinson Avenue, Cloverdale WA 6105".format(index + 2),
                delivery="{0}a Brown Street, Middle Swan WA 6056".format(index + 5),
                notes="WEEKLY_NOTES_SECRET {0}".format(index),
            )

    weekly = weekly_schedule_data.build_weekly_schedule(monday.isoformat())
    names = [
        [job["customer_name"] for job in day["jobs"] if job["customer_name"].startswith(prefix)]
        for day in weekly["days"]
    ]
    assert names[3] == []
    assert names[4] == []
    assert names[1] == [prefix + "Solo Tuesday"]
    assert len(names[0]) == 4
    assert len(names[5]) == 4
    assert len(names[6]) == 4
    pdf_bytes = weekly_schedule_pdf.render_weekly_schedule_pdf(weekly)
    pages = _page_count(pdf_bytes)
    assert pages == 1, "Expected 1 page, got {0}".format(pages)
    _x0, _y0, width, height = _pdf_mediabox(pdf_bytes)
    assert width > height, (width, height)
    assert abs(width - 841.89) < 1.0, width
    assert abs(height - 595.28) < 1.0, height
    text = _pdf_plain_text(pdf_bytes)
    assert "Japanese Removals" in text
    assert "WEEKLY SCHEDULE" in text
    assert monday.strftime("%-d").upper() in text or str(monday.day) in text
    assert "NO JOBS" in text
    assert prefix + "Solo Tuesday" in text
    assert "WEEKLY_NOTES_SECRET" not in text
    assert "2hr" in text
    preview = _save_pdf_preview(pdf_bytes, "weekly-schedule-busy-week.pdf")
    print("saved busy pdf preview", preview)

    client = _login_client()
    response = client.get("/calendar/weekly/{0}/schedule.pdf".format(monday.isoformat()))
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert _page_count(response.data) == 1
    assert "attachment" in response.headers.get("Content-Disposition", "")
    assert "weekly-schedule-{0}.pdf".format(monday.isoformat()) in response.headers.get(
        "Content-Disposition", ""
    )
    return True


def test_weekly_pdf_example_layout():
    monday = date(2027, 8, 23)
    assert monday.weekday() == 0
    _create_booking(
        "Liam Warn",
        monday.isoformat(),
        "08:00",
        "10:00",
        pickup="2/280 Robinson Avenue, Cloverdale",
        delivery="5a Brown Street, Middle Swan",
    )
    _create_booking(
        "John",
        monday.isoformat(),
        "11:35",
        "17:35",
        pickup="3/279 Belgravia Street, Cloverdale",
        delivery="11 Torquata Street, Piara Waters",
    )
    _create_booking(
        "Saturday Customer",
        (monday + timedelta(days=5)).isoformat(),
        "08:15",
        "10:45",
    )
    weekly = weekly_schedule_data.build_weekly_schedule(
        monday.isoformat(),
        reference=date(2027, 8, 1),
    )
    assert weekly["range_heading"] == "23 AUG – 29 AUG 2027"
    assert weekly["days"][1]["is_empty"] is True
    pdf_bytes = weekly_schedule_pdf.render_weekly_schedule_pdf(weekly)
    assert _page_count(pdf_bytes) == 1
    text = _pdf_plain_text(pdf_bytes)
    assert "Liam Warn" in text
    assert "John" in text
    assert "2hr" in text
    assert "6hr" in text
    assert "2.5hr" in text
    assert "NO JOBS" in text
    assert "SATURDAY 28 AUG" in text
    preview = _save_pdf_preview(pdf_bytes, "weekly-schedule-example.pdf")
    print("saved example pdf preview", preview)
    return True


def test_calendar_and_nav_include_weekly_schedule():
    client = _login_client()
    calendar_html = client.get("/calendar?view=month&year=2097&month=4").get_data(
        as_text=True
    )
    assert "Weekly Schedule" in calendar_html
    assert "/calendar/weekly/" in calendar_html
    assert "Daily Jobs" in client.get(
        "/calendar?view=day&year=2097&month=4&day=1"
    ).get_data(as_text=True)
    daily = client.get("/calendar/daily/2097-04-01").get_data(as_text=True)
    assert "Daily Jobs" in daily
    nav = (ROOT / "templates" / "_navigation.html").read_text()
    assert "calendar_weekly_schedule" in nav
    return True


def test_weekly_screen_layout_desktop_and_phone():
    monday = date(2097, 8, 5)
    assert monday.weekday() == 0
    _create_booking("Liam Warn", monday.isoformat(), "08:00", "10:00")
    _create_booking("John", monday.isoformat(), "11:35", "17:35")
    client = _login_client()
    html = client.get("/calendar/weekly/{0}".format(monday.isoformat())).get_data(
        as_text=True
    )
    style = (ROOT / "static" / "style.css").read_text()
    mobile = (ROOT / "static" / "mobile.css").read_text()
    weekly_css = (ROOT / "static" / "weekly_schedule.css").read_text()
    html = re.sub(r'<link rel="stylesheet"[^>]*>', "", html)
    html = html.replace(
        "</head>",
        "<style>{0}</style>\n<style>{1}</style>\n<style>{2}</style>\n</head>".format(
            style, mobile, weekly_css
        ),
        1,
    )

    from playwright.sync_api import sync_playwright

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    measure_js = """
    () => {
      const title = document.querySelector('.weekly-schedule-title');
      const nav = document.querySelector('.weekly-schedule-nav');
      const days = [...document.querySelectorAll('.weekly-day')];
      const times = [...document.querySelectorAll('.weekly-job-time')].map((el) => {
        const cs = getComputedStyle(el);
        const box = el.getBoundingClientRect();
        return {
          text: (el.textContent || '').replace(/\\s+/g, ' ').trim(),
          whiteSpace: cs.whiteSpace,
          flexWrap: cs.flexWrap,
          height: Math.round(box.height * 10) / 10,
        };
      });
      return {
        title: title ? (title.textContent || '').trim() : '',
        navButtons: nav ? [...nav.querySelectorAll('a')].map((a) => (a.textContent || '').trim()) : [],
        dayCount: days.length,
        emptyDays: days.filter((day) => (day.textContent || '').includes('NO JOBS')).length,
        headings: days.map((day) => (day.querySelector('.weekly-day-heading') || {}).textContent || ''),
        times,
        pageWidth: document.documentElement.clientWidth,
      };
    }
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        for name, width, height, filename in (
            ("desktop", 1280, 900, "weekly-schedule-desktop.png"),
            ("phone", 390, 844, "weekly-schedule-phone.png"),
        ):
            page.set_viewport_size({"width": width, "height": height})
            page.set_content(html, wait_until="load")
            measured = page.evaluate(measure_js)
            assert measured["title"] == "WEEKLY SCHEDULE", measured
            assert measured["navButtons"] == [
                "← Previous Week",
                "This Week",
                "Next Week →",
            ], measured
            assert measured["dayCount"] == 7, measured
            assert measured["emptyDays"] == 6, measured
            assert "MONDAY 5 AUG" in measured["headings"][0]
            assert "SUNDAY 11 AUG" in measured["headings"][6]
            assert measured["times"], measured
            for time_row in measured["times"]:
                assert time_row["whiteSpace"] == "nowrap", time_row
                assert time_row["flexWrap"] == "nowrap", time_row
                assert "8:00 AM – 10:00 AM" in time_row["text"] or "11:35 AM – 5:35 PM" in time_row["text"]
                assert "2hr" in time_row["text"] or "6hr" in time_row["text"]
            page.screenshot(
                path=str(SCREENSHOT_DIR / filename),
                full_page=True,
            )
            print(name, "width=", measured["pageWidth"], "days=", measured["dayCount"])
        browser.close()
    return True


def main():
    tests = [
        test_monday_to_sunday_order_and_empty_days,
        test_month_spanning_week_heading,
        test_cross_year_week_heading,
        test_weekly_page_nav_and_fields,
        test_weekly_pdf_one_landscape_page_even_when_busy,
        test_weekly_pdf_example_layout,
        test_calendar_and_nav_include_weekly_schedule,
        test_weekly_screen_layout_desktop_and_phone,
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
            print("FAIL:", test.__name__, "—", exc)
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
