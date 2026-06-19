#!/usr/bin/env python3
"""Verify calendar SMS link HTML (single clickable anchor, no Messages line)."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booking_helpers import build_calendar_description, sms_href
from crew import display_crew


def visible_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def main() -> int:
    phone = "0432 393 117"
    booking = {
        "id": 16,
        "phone": phone,
        "email": "customer@example.com",
        "pickup_address": "1 St, Perth, WA",
        "delivery_address": "2 St, Perth, WA",
        "crew": "Yasu,Tom",
        "notes": "",
    }
    assert sms_href(phone) == "sms:0432393117"

    html = build_calendar_description(booking, display_crew=display_crew)
    vis = visible_text(html)

    assert '<a href="sms:0432393117">💬 Text Customer</a>' in html
    assert "Messages:" not in html
    assert "sms:" not in vis
    assert "0432393117" not in vis
    assert "💬 Text Customer" in vis
    assert "📞 Call Customer" in vis
    assert "✉️ Email Customer" in vis
    assert "Notes:" not in vis
    assert "Booking #16" in vis
    assert "&lt;a href" not in html
    assert "maps.apple.com" in html

    print("OK")
    print(html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
