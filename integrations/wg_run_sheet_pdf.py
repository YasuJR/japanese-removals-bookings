"""WG Run Sheet PDF (/staff/wg/<id>/run-sheet.pdf): one WG Day, every stop in time order.

A plain crew run sheet, not an invoice: big stop number, time and customer,
then Products / Address / Phone / Payment / REX / Notes. A4 portrait so it reads
on an iPhone and prints well. Each stop is kept on one page when it fits.
Built only from wg_orders.run_sheet(), the same data as the Staff Portal page.
"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any, Dict, List
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from config import COMPANY_NAME
from integrations.weekly_schedule_pdf import CJK_FONT, FONT, FONT_BOLD

GREEN = colors.HexColor("#1f5f3a")
MUTED = colors.HexColor("#5a6470")
_CJK_RE = re.compile(r"([　-ヿ㐀-鿿＀-￯]+)")


def _markup(value: Any) -> str:
    """Escape for Paragraph markup, keep line breaks, Japanese in the CID font."""
    text = str(value or "").replace("\r\n", "\n").strip()
    parts = _CJK_RE.split(text)
    out: List[str] = []
    for index, part in enumerate(parts):
        if not part:
            continue
        safe = escape(part).replace("\n", "<br/>")
        if index % 2 == 1 and CJK_FONT:
            out.append('<font face="{0}">{1}</font>'.format(CJK_FONT, safe))
        else:
            out.append(safe)
    return "".join(out)


def _styles() -> Dict[str, ParagraphStyle]:
    return {
        "company": ParagraphStyle("wgr_company", fontName=FONT, fontSize=10, leading=13, textColor=MUTED),
        "title": ParagraphStyle("wgr_title", fontName=FONT_BOLD, fontSize=22, leading=26, textColor=GREEN),
        "date": ParagraphStyle("wgr_date", fontName=FONT_BOLD, fontSize=17, leading=21),
        "meta": ParagraphStyle("wgr_meta", fontName=FONT, fontSize=13, leading=17),
        "stop": ParagraphStyle("wgr_stop", fontName=FONT_BOLD, fontSize=15, leading=18, textColor=GREEN, spaceBefore=4),
        "time": ParagraphStyle("wgr_time", fontName=FONT_BOLD, fontSize=17, leading=21),
        "customer": ParagraphStyle("wgr_customer", fontName=FONT_BOLD, fontSize=18, leading=22, spaceAfter=4),
        "label": ParagraphStyle("wgr_label", fontName=FONT_BOLD, fontSize=10, leading=12, textColor=MUTED, spaceBefore=5),
        "value": ParagraphStyle("wgr_value", fontName=FONT, fontSize=13, leading=17),
        "ref": ParagraphStyle("wgr_ref", fontName=FONT_BOLD, fontSize=14, leading=18),
    }


def _stop_block(stop: Dict[str, Any], st: Dict[str, ParagraphStyle]) -> List[Any]:
    parts: List[Any] = [
        Paragraph("STOP {0}".format(stop["number"]), st["stop"]),
        Paragraph(_markup(stop["time_range"]), st["time"]),
        Paragraph(_markup(stop.get("customer") or "—"), st["customer"]),
    ]
    rows = (
        ("Products", stop.get("products"), "value"),
        ("Address", stop.get("address") or "—", "value"),
        ("Phone", stop.get("phone") or "—", "value"),
        ("Payment", stop.get("payment_display"), "value"),
        ("REX", stop.get("reference"), "ref"),
        ("Notes", stop.get("notes"), "value"),
    )
    for label, value, style in rows:
        if not value:
            continue
        parts.append(Paragraph(label.upper(), st["label"]))
        parts.append(Paragraph(_markup(value), st[style]))
    return parts


def render_wg_run_sheet_pdf(sheet: Dict[str, Any]) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="WG Run Sheet {0}".format(sheet.get("move_date") or ""),
        author=COMPANY_NAME,
    )
    st = _styles()
    story: List[Any] = [
        Paragraph(_markup(COMPANY_NAME), st["company"]),
        Paragraph("WG OUTDOOR LIFE", st["title"]),
        Paragraph(_markup(sheet.get("date_heading")), st["date"]),
        Spacer(1, 4),
        Paragraph("<b>Crew:</b> {0}".format(_markup(sheet.get("crew_display"))), st["meta"]),
        Paragraph("<b>Truck:</b> {0}".format(_markup(sheet.get("truck"))), st["meta"]),
        Paragraph("{0} · {1}".format(_markup(sheet.get("stops_label")), _markup(sheet.get("time_range"))), st["meta"]),
    ]
    for stop in sheet.get("stops") or []:
        story.append(Spacer(1, 6))
        story.append(HRFlowable(width="100%", thickness=1.2, color=GREEN, spaceBefore=2, spaceAfter=4))
        story.append(KeepTogether(_stop_block(stop, st)))
    if not sheet.get("stops"):
        story.append(Spacer(1, 10))
        story.append(Paragraph("No stops on this WG Day yet.", st["value"]))
    doc.build(story)
    return buffer.getvalue()
