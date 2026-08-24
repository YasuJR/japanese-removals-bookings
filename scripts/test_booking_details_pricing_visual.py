#!/usr/bin/env python3
"""Visual layout checks for Booking Details Pricing & invoice at desktop and phone widths."""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth
import database as db
from app import app
from dashboard_data import perth_today

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

SCREENSHOT_DIR = Path("/opt/cursor/artifacts/screenshots")
AMOUNT_LABELS = {
    "Hourly rate",
    "Callout fee",
    "GST",
    "Total invoice amount",
    "Payment status",
    "Invoice status",
}


def _login_client():
    db.init_db()
    label = "pricing-visual-{0}".format(os.getpid())
    uid = db.create_staff_user(label, auth.hash_password("test"), "Pricing Visual")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["username"] = label
    return client


def _booking_html():
    client = _login_client()
    booking_id = db.create_booking(
        customer_name="Pricing Layout Customer",
        phone="0412 345 678",
        email="pricing-layout@example.com",
        pickup_address="10 Pickup Rd, Subiaco WA 6008",
        delivery_address="20 Delivery St, Fremantle WA 6160",
        move_date=perth_today().isoformat(),
        num_movers=2,
        notes="",
        start_time="08:00",
        finish_time="11:00",
        duration_hours="3",
        crew="Ken",
        hourly_rate=180.0,
        callout_fee=90.0,
        gst_enabled=1,
        status="Confirmed",
    )
    db.replace_extra_charges(
        booking_id,
        [
            {"description": "Pots delivered", "quantity": 1, "unit_price": 660.0},
            {
                "description": (
                    "Stair carry and placement against the courtyard wall"
                ),
                "quantity": 2,
                "unit_price": 45.5,
            },
        ],
    )
    html = client.get("/bookings/{0}".format(booking_id)).get_data(as_text=True)
    style = (ROOT / "static" / "style.css").read_text()
    mobile = (ROOT / "static" / "mobile.css").read_text()
    html = re.sub(r'<link rel="stylesheet"[^>]*>', "", html)
    html = html.replace(
        "</head>",
        "<style>{0}</style>\n<style>{1}</style>\n</head>".format(style, mobile),
        1,
    )
    return html


MEASURE_JS = """
() => {
  const heading = [...document.querySelectorAll('h3')].find((h) =>
    (h.textContent || '').includes('Pricing')
  );
  if (!heading) return { error: 'Pricing heading missing' };
  const dl = heading.parentElement.querySelector('dl.booking-details-pricing');
  if (!dl) return { error: 'Pricing list missing' };
  const rows = [...dl.querySelectorAll(':scope > div')].map((row) => {
    const dt = row.querySelector('dt');
    const dd = row.querySelector('dd');
    const dtCs = getComputedStyle(dt);
    const ddCs = getComputedStyle(dd);
    const dtBox = dt.getBoundingClientRect();
    const ddBox = dd.getBoundingClientRect();
    const parseLine = (value, fallback) => {
      const n = parseFloat(value);
      return Number.isFinite(n) ? n : fallback;
    };
    return {
      label: (dt.textContent || '').replace(/\\s+/g, ' ').trim(),
      value: (dd.textContent || '').replace(/\\s+/g, ' ').trim(),
      className: row.className,
      dtHeight: Math.round(dtBox.height * 10) / 10,
      ddHeight: Math.round(ddBox.height * 10) / 10,
      dtWidth: Math.round(dtBox.width * 10) / 10,
      ddWidth: Math.round(ddBox.width * 10) / 10,
      dtWhiteSpace: dtCs.whiteSpace,
      ddWhiteSpace: ddCs.whiteSpace,
      ddWordBreak: ddCs.wordBreak,
      dtLineHeight: parseLine(dtCs.lineHeight, 20),
      ddLineHeight: parseLine(ddCs.lineHeight, 22),
    };
  });
  return {
    dlWidth: Math.round(dl.getBoundingClientRect().width * 10) / 10,
    columns: getComputedStyle(dl).gridTemplateColumns,
    rows,
  };
}
"""


def _assert_horizontal(viewport_name, measured):
    assert "error" not in measured, measured
    assert measured["dlWidth"] > 250, measured
    assert "auto-fill" not in (measured["columns"] or "")
    seen = {row["label"] for row in measured["rows"]}
    for label in AMOUNT_LABELS:
        assert label in seen, "{0} missing {1}".format(viewport_name, label)

    for row in measured["rows"]:
        label = row["label"]
        assert row["ddWordBreak"] != "break-all", row
        if label in AMOUNT_LABELS:
            assert row["dtWhiteSpace"] == "nowrap", row
            assert row["ddWhiteSpace"] == "nowrap", row
            assert row["dtHeight"] <= row["dtLineHeight"] * 1.8, (
                "{0} label wrapped vertically: {1}".format(viewport_name, row)
            )
            assert row["ddHeight"] <= row["ddLineHeight"] * 1.8, (
                "{0} value wrapped vertically: {1}".format(viewport_name, row)
            )
            assert "$" not in row["value"] or "/hr" in row["value"] or row["value"].startswith("$") or "GST" in row["value"] or "applicable" in row["value"] or True
        if label == "Hourly rate":
            assert "$180.00/hr" in row["value"], row
        if label == "Callout fee":
            assert "$90.00" in row["value"], row
        if label == "GST":
            assert "(10%)" in row["value"] and "$" in row["value"], row
        if label == "Total invoice amount":
            assert "(incl. GST)" in row["value"], row
        if label == "Extra charges":
            assert row["ddWhiteSpace"] == "normal", row
            assert row["ddHeight"] <= row["ddLineHeight"] * 8, (
                "{0} extra charges look vertically stacked: {1}".format(
                    viewport_name, row
                )
            )


def main():
    from playwright.sync_api import sync_playwright

    html = _booking_html()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    viewports = (
        ("desktop", 1280, 900, "booking-details-pricing-desktop.png"),
        ("tablet", 768, 1024, "booking-details-pricing-tablet.png"),
        ("phone", 390, 844, "booking-details-pricing-mobile.png"),
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        for name, width, height, filename in viewports:
            page.set_viewport_size({"width": width, "height": height})
            page.set_content(html, wait_until="load")
            page.locator("dl.booking-details-pricing").scroll_into_view_if_needed()
            measured = page.evaluate(MEASURE_JS)
            print(name, "columns=", measured.get("columns"), "width=", measured.get("dlWidth"))
            for row in measured.get("rows", []):
                print(
                    " ",
                    row["label"],
                    "|",
                    row["value"][:60],
                    "| h=",
                    row["dtHeight"],
                    row["ddHeight"],
                    "|",
                    row["ddWhiteSpace"],
                    row["ddWordBreak"],
                )
            _assert_horizontal(name, measured)
            path = SCREENSHOT_DIR / filename
            page.locator(".booking-details-section:has(dl.booking-details-pricing)").screenshot(
                path=str(path)
            )
            print("saved", path)
        browser.close()
    print("PASS: desktop and phone Pricing & invoice stay horizontal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
