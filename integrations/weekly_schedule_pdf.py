"""Compact A4 landscape weekly schedule PDF (read-only over existing bookings)."""

from __future__ import annotations

import re
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepInFrame,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from config import COMPANY_NAME

_DEJAVU_DIR = Path("/usr/share/fonts/truetype/dejavu")
if (_DEJAVU_DIR / "DejaVuSans.ttf").exists() and (_DEJAVU_DIR / "DejaVuSans-Bold.ttf").exists():
    pdfmetrics.registerFont(TTFont("WsSans", str(_DEJAVU_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("WsSans-Bold", str(_DEJAVU_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(
        "WsSans",
        normal="WsSans",
        bold="WsSans-Bold",
        italic="WsSans",
        boldItalic="WsSans-Bold",
    )
    FONT = "WsSans"
    FONT_BOLD = "WsSans-Bold"
    FONT_OBLIQUE = "WsSans"
    _UNICODE_FONT = True
else:
    FONT = "Helvetica"
    FONT_BOLD = "Helvetica-Bold"
    FONT_OBLIQUE = "Helvetica-Oblique"
    _UNICODE_FONT = False

PAGE = landscape(A4)
MARGIN = 7 * mm
HEADER_H = 20 * mm
INNER_W = PAGE[0] - 2 * MARGIN
INNER_H = PAGE[1] - 2 * MARGIN
BODY_H = INNER_H - HEADER_H
COL_W = INNER_W / 7.0

NAVY = colors.HexColor("#1e3a5f")
HEADER_BG = colors.HexColor("#1e3a5f")
WEEKEND_BG = colors.HexColor("#eef3f8")
EMPTY_FG = colors.HexColor("#7a8694")
LINE = colors.HexColor("#c5d0dc")
BODY = colors.HexColor("#1a2330")
MUTED = colors.HexColor("#4a5563")


def _pdf_text(value: Any) -> str:
    text = str(value or "")
    if _UNICODE_FONT:
        return text
    return (
        text.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u00a0", " ")
    )


def _esc(value: Any) -> str:
    return escape(_pdf_text(value))


def _styles(scale: float = 1.0) -> dict[str, ParagraphStyle]:
    s = max(0.72, min(1.0, scale))
    return {
        "day": ParagraphStyle(
            "ws_day",
            fontName=FONT_BOLD,
            fontSize=7.2 * s,
            leading=9 * s,
            alignment=TA_CENTER,
            textColor=colors.white,
        ),
        "job": ParagraphStyle(
            "ws_job",
            fontName=FONT,
            fontSize=6.4 * s,
            leading=7.8 * s,
            alignment=TA_LEFT,
            textColor=BODY,
            spaceBefore=0,
            spaceAfter=0,
        ),
        "empty": ParagraphStyle(
            "ws_empty",
            fontName=FONT_OBLIQUE,
            fontSize=7 * s,
            leading=9 * s,
            alignment=TA_CENTER,
            textColor=EMPTY_FG,
        ),
    }


def _job_html(job: dict) -> str:
    time_range = _esc(job.get("time_range") or "Time TBC")
    duration = _esc(job.get("duration_label") or "")
    time_line = time_range
    if duration:
        time_line = "{0}&nbsp;&nbsp;<b>{1}</b>".format(time_range, duration)

    name = _esc(job.get("customer_name") or "—")
    movers = _esc(job.get("num_movers") or "—")
    crew = _esc(job.get("crew_display") or "Unassigned")
    pickup = _esc(job.get("pickup_address") or "—")
    delivery = _esc(job.get("delivery_address") or "—")
    phone = _esc(job.get("phone") or "—")
    status = _esc(str(job.get("status") or "confirmed").replace("_", " ").upper())

    parts = [
        "<b>{0}</b>".format(time_line),
        "<b>{0}</b>".format(name),
        "Movers: {0} &nbsp; Crew: {1}".format(movers, crew),
        "P: {0}".format(pickup),
        "D: {0}".format(delivery),
        "Ph: {0}".format(phone),
        "Status: {0}".format(status),
    ]
    return "<br/>".join(parts)


def _day_body(day: dict, styles: dict[str, ParagraphStyle]) -> Paragraph:
    jobs = day.get("jobs") or []
    if not jobs:
        return Paragraph("NO JOBS", styles["empty"])
    blocks = [_job_html(job) for job in jobs]
    html = "<br/><font color='#c5d0dc'>----------</font><br/>".join(blocks)
    return Paragraph(html, styles["job"])


def _build_grid(schedule: dict, scale: float = 1.0, day_body_fn=None) -> list:
    if day_body_fn is None:
        day_body_fn = _day_body
    styles = _styles(scale)
    header_row = [
        Paragraph(_esc(day.get("heading") or ""), styles["day"])
        for day in schedule["days"]
    ]
    body_row = [day_body_fn(day, styles) for day in schedule["days"]]

    header_table = Table(
        [header_row],
        colWidths=[COL_W] * 7,
        rowHeights=[8.6 * mm * max(0.85, scale)],
    )
    header_cmds = [
        ("BACKGROUND", (0, 0), (-1, -1), HEADER_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1.2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.2),
        ("TOPPADDING", (0, 0), (-1, -1), 1.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
        ("BOX", (0, 0), (-1, -1), 0.4, NAVY),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.white),
    ]
    for idx, day in enumerate(schedule["days"]):
        if day.get("is_weekend"):
            header_cmds.append(("BACKGROUND", (idx, 0), (idx, 0), colors.HexColor("#16304d")))
    header_table.setStyle(TableStyle(header_cmds))

    body_table = Table([body_row], colWidths=[COL_W] * 7)
    body_cmds = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1.8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.8),
        ("TOPPADDING", (0, 0), (-1, -1), 2.0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.0),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
    ]
    for idx, day in enumerate(schedule["days"]):
        if day.get("is_weekend"):
            body_cmds.append(("BACKGROUND", (idx, 0), (idx, 0), WEEKEND_BG))
        if day.get("is_empty"):
            body_cmds.append(("VALIGN", (idx, 0), (idx, 0), "MIDDLE"))
    body_table.setStyle(TableStyle(body_cmds))
    return [header_table, Spacer(1, 0.5 * mm), body_table]


def _draw_page_header(canvas, _doc, schedule: dict) -> None:
    _draw_schedule_header(
        canvas,
        title="WEEKLY SCHEDULE",
        subtitle=schedule.get("range_heading") or "",
        footer_lines=[],
    )


def _draw_schedule_header(
    canvas,
    *,
    title: str,
    subtitle: str = "",
    subtitle_lines: list[str] | None = None,
    footer_lines: list[str] | None = None,
) -> None:
    canvas.saveState()
    centre = PAGE[0] / 2.0
    y = PAGE[1] - MARGIN - 3.8 * mm
    canvas.setFillColor(NAVY)
    canvas.setFont(FONT_BOLD, 11)
    canvas.drawCentredString(centre, y, _pdf_text(COMPANY_NAME))
    y -= 5.0 * mm
    canvas.setFont(FONT_BOLD, 12.5)
    canvas.drawCentredString(centre, y, _pdf_text(title))
    y -= 4.6 * mm
    canvas.setFillColor(MUTED)
    canvas.setFont(FONT, 8.8)
    lines = list(subtitle_lines or [])
    if not lines and subtitle:
        lines = [subtitle]
    for line in lines:
        canvas.drawCentredString(centre, y, _pdf_text(line))
        y -= 4.0 * mm
    for line in footer_lines or []:
        canvas.drawCentredString(centre, y, _pdf_text(line))
        y -= 3.6 * mm
    canvas.restoreState()


# --- Staff Portal weekly PDF (All Staff + Individual) -------------------------
#
# Data comes from staff_portal.build_staff_weekly_pdf_schedule, which reuses the
# Weekly screen builder. This section is presentation only: one card per job,
# Monday→Sunday columns, fitted onto one A4 landscape page by stepping the font
# size down to a readable floor. If a week still does not fit at the floor, the
# grid continues on the next page (header row repeated) instead of cutting jobs.

try:
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    CJK_FONT = "HeiseiKakuGo-W5"
except Exception:  # pragma: no cover - reportlab always ships this CID font
    CJK_FONT = ""

STAFF_SCALES = (1.0, 0.93, 0.86, 0.8, 0.75)
STAFF_JOB_FONT = 7.4
STAFF_LINE_RATIO = 1.2
STAFF_CELL_PAD = 2.0
STAFF_CARD_PAD = 2.2
STAFF_HEADER_H = 22 * mm

_CJK_RE = re.compile(
    "([\u2e80-\u2fdf\u3000-\u30ff\u3100-\u31ff\u3400-\u4dbf"
    "\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]+)"
)
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")

CARD_BG = colors.HexColor("#f7f9fb")
CARD_LINE = colors.HexColor("#d5dde6")
STATUS_COLOURS = {
    "COMPLETED": "#1f7a3f",
    "PAID": "#1f7a3f",
    "INVOICED": "#1f6f8b",
    "CONFIRMED": "#1e3a5f",
    "CANCELLED": "#a12a2a",
}


def _clean_text(value: Any) -> str:
    text = _INVISIBLE_RE.sub("", str(value if value is not None else ""))
    return " ".join(text.split())


def _char_font(ch: str, font: str) -> str:
    if CJK_FONT and _CJK_RE.match(ch):
        return CJK_FONT
    return font


def _text_width(text: str, font: str, size: float) -> float:
    return sum(
        pdfmetrics.stringWidth(_pdf_text(ch), _char_font(ch, font), size) for ch in text
    )


def _break_long_words(
    text: str, font: str, size: float, width: float, lead_width: float = 0.0
) -> str:
    """Insert spaces inside words wider than the column so they wrap, not overflow.

    ReportLab only breaks lines at spaces, so a long unspaced token (a run of
    Japanese, a long URL or address without spaces) would otherwise spill into
    the next day's column. Lines are simulated greedily, starting after a label
    of lead_width (e.g. "P: "), so a split token fills the current line first.
    """
    space_w = _text_width(" ", font, size)
    used = lead_width
    out: list[str] = []
    for word in text.split(" "):
        if not word:
            continue
        word_w = _text_width(word, font, size)
        gap = space_w if used > 0 else 0.0
        if used + gap + word_w <= width:
            out.append(word)
            used += gap + word_w
            continue
        if word_w <= width:
            out.append(word)
            used = word_w
            continue
        # Split: first chunk fills what is left of this line (if anything useful).
        chunk = ""
        room = width - used - gap if used > 0 else width
        if room < width * 0.25:
            room = width
            used = 0.0
        for ch in word:
            if chunk and _text_width(chunk + ch, font, size) > room:
                out.append(chunk)
                chunk = ch
                room = width
            else:
                chunk += ch
        out.append(chunk)
        used = _text_width(chunk, font, size)
    return " ".join(out)


def _markup(value: Any, font: str, size: float, width: float, lead: str = "") -> str:
    """Escape for Paragraph markup; switch CJK runs to the Japanese CID font."""
    lead_width = _text_width(lead, font, size) if lead else 0.0
    text = _break_long_words(_clean_text(value), font, size, width, lead_width)
    parts = _CJK_RE.split(text)
    html_parts: list[str] = []
    for idx, part in enumerate(parts):
        if not part:
            continue
        if idx % 2 == 1 and CJK_FONT:
            html_parts.append('<font face="{0}">{1}</font>'.format(CJK_FONT, _esc(part)))
        else:
            html_parts.append(_esc(part))
    return "".join(html_parts)


def _staff_styles(scale: float) -> dict[str, ParagraphStyle]:
    size = STAFF_JOB_FONT * scale
    return {
        "size": size,
        "day": ParagraphStyle(
            "sw_day",
            fontName=FONT_BOLD,
            fontSize=7.4,
            leading=9,
            alignment=TA_CENTER,
            textColor=colors.white,
        ),
        "job": ParagraphStyle(
            "sw_job",
            fontName=FONT,
            fontSize=size,
            leading=size * STAFF_LINE_RATIO,
            alignment=TA_LEFT,
            textColor=BODY,
        ),
        "empty": ParagraphStyle(
            "sw_empty",
            fontName=FONT_OBLIQUE,
            fontSize=7.4,
            leading=9,
            alignment=TA_CENTER,
            textColor=EMPTY_FG,
        ),
        "paid": ParagraphStyle(
            "sw_paid",
            fontName=FONT_BOLD,
            fontSize=size,
            leading=size * STAFF_LINE_RATIO,
            alignment=TA_LEFT,
            textColor=NAVY,
        ),
    }


def _hours_line(job: dict) -> list[str]:
    parts = ["Actual {0}".format(job.get("actual_hours_display") or "—")]
    if job.get("break_hours_label"):
        parts.append("Break {0}".format(job["break_hours_label"]))
    if job.get("callout_hours_label"):
        parts.append("Call Out {0}".format(job["callout_hours_label"]))
    if job.get("extra_pay_hours_label"):
        parts.append("Extra Pay {0}".format(job["extra_pay_hours_label"]))
    return parts


def _staff_job_card(job: dict, styles: dict, width: float) -> Table:
    size = styles["size"]
    text_w = width - 2 * STAFF_CARD_PAD

    def m(value: Any, font: str = FONT, lead: str = "") -> str:
        return _markup(value, font, size, text_w, lead)

    status = _clean_text(job.get("status")).upper()
    time_line = "<b>{0}</b>".format(m(job.get("time_range") or "TIME TBC", FONT_BOLD))
    if job.get("duration_label"):
        time_line += " &nbsp;{0}".format(m(job["duration_label"]))
    lines = [time_line]
    if status:
        lines.append(
            '<font color="{0}"><b>{1}</b></font>'.format(
                STATUS_COLOURS.get(status, "#4a5563"), m(status, FONT_BOLD)
            )
        )
    lines.append("<b>{0}</b>".format(m(job.get("customer_name") or "—", FONT_BOLD)))
    crew = "Crew: {0}".format(m(job.get("crew_display") or "—", lead="Crew: "))
    if job.get("show_movers") and job.get("num_movers"):
        crew = "Movers: {0} · {1}".format(m(job["num_movers"]), crew)
    lines.append(crew)
    lines.append("P: {0}".format(m(job.get("pickup_address") or "—", lead="P: ")))
    lines.append("D: {0}".format(m(job.get("delivery_address") or "—", lead="D: ")))
    lines.append("Ph: {0}".format(m(job.get("phone") or "—", lead="Ph: ")))
    if job.get("notes"):
        lines.append("<i>Note: {0}</i>".format(m(job["notes"], lead="Note: ")))
    lines.append(m(" · ".join(_hours_line(job))))
    lines.append(
        '<font color="#1e3a5f"><b>Paid {0}</b></font>'.format(
            m(job.get("paid_hours_display") or "—", FONT_BOLD)
        )
    )
    card = Table(
        [[Paragraph("<br/>".join(lines), styles["job"])]],
        colWidths=[width],
    )
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CARD_BG),
                ("BOX", (0, 0), (-1, -1), 0.4, CARD_LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), STAFF_CARD_PAD),
                ("RIGHTPADDING", (0, 0), (-1, -1), STAFF_CARD_PAD),
                ("TOPPADDING", (0, 0), (-1, -1), STAFF_CARD_PAD),
                ("BOTTOMPADDING", (0, 0), (-1, -1), STAFF_CARD_PAD),
            ]
        )
    )
    return card


def _staff_day_cell(day: dict, styles: dict) -> list:
    jobs = day.get("jobs") or []
    if not jobs:
        return [Paragraph("NO JOBS", styles["empty"])]
    width = COL_W - 2 * STAFF_CELL_PAD
    flow: list = []
    for idx, job in enumerate(jobs):
        if idx:
            flow.append(Spacer(1, 1.6 * mm))
        flow.append(_staff_job_card(job, styles, width))
    if day.get("paid_display") is not None:
        flow.append(Spacer(1, 1.2 * mm))
        flow.append(
            Paragraph("Daily Paid: {0}".format(_esc(day["paid_display"])), styles["paid"])
        )
    return flow


def _staff_grid(schedule: dict, scale: float) -> Table:
    styles = _staff_styles(scale)
    days = schedule["days"]
    header = [Paragraph(_esc(day.get("heading") or ""), styles["day"]) for day in days]
    body = [_staff_day_cell(day, styles) for day in days]
    grid = Table(
        [header, body],
        colWidths=[COL_W] * 7,
        repeatRows=1,
        splitInRow=1,
    )
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("VALIGN", (0, 1), (-1, 1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), STAFF_CELL_PAD),
        ("RIGHTPADDING", (0, 0), (-1, -1), STAFF_CELL_PAD),
        ("TOPPADDING", (0, 0), (-1, 0), 2.6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2.6),
        ("TOPPADDING", (0, 1), (-1, 1), STAFF_CELL_PAD),
        ("BOTTOMPADDING", (0, 1), (-1, 1), STAFF_CELL_PAD),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
    ]
    for idx, day in enumerate(days):
        if day.get("is_weekend"):
            cmds.append(("BACKGROUND", (idx, 0), (idx, 0), colors.HexColor("#16304d")))
            cmds.append(("BACKGROUND", (idx, 1), (idx, 1), WEEKEND_BG))
    grid.setStyle(TableStyle(cmds))
    return grid


def _fit_staff_grid(schedule: dict, avail_h: float) -> tuple[Table, float, bool]:
    """Largest scale whose grid fits on one page; else the floor scale, split."""
    for scale in STAFF_SCALES:
        grid = _staff_grid(schedule, scale)
        _w, height = grid.wrap(INNER_W, avail_h)
        if height <= avail_h:
            return grid, scale, True
    floor = STAFF_SCALES[-1]
    return _staff_grid(schedule, floor), floor, False


def _draw_staff_page_header(canvas, _doc, schedule: dict) -> None:
    _draw_schedule_header(
        canvas,
        title=schedule.get("title") or "WEEKLY STAFF SCHEDULE",
        subtitle_lines=list(schedule.get("subtitle_lines") or []),
    )


def render_staff_weekly_schedule_pdf(schedule: dict) -> bytes:
    """Render a Staff Portal weekly schedule (All Staff or Individual).

    Fits on one A4 landscape page whenever the text stays at or above the
    readable floor (STAFF_SCALES[-1] × STAFF_JOB_FONT ≈ 5.6pt). A busier week
    continues on a second page; nothing is cropped.
    """
    header_h = STAFF_HEADER_H + max(0, len(schedule.get("subtitle_lines") or []) - 1) * 4.0 * mm
    avail_h = INNER_H - header_h
    grid, scale, fits = _fit_staff_grid(schedule, avail_h)
    schedule["_fit_scale"] = scale
    schedule["_fits_one_page"] = fits
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=PAGE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN + header_h,
        bottomMargin=MARGIN,
        title="{0} {1}".format(
            schedule.get("title") or "Weekly Schedule", schedule.get("range_heading") or ""
        ).strip(),
        author=COMPANY_NAME,
    )
    # Zero-padding frame: exactly the INNER_W × avail_h area the fit check measured.
    frame = Frame(
        MARGIN,
        MARGIN,
        INNER_W,
        avail_h,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        id="staff_week",
    )
    doc.addPageTemplates(
        [
            PageTemplate(
                id="staff_week",
                frames=[frame],
                onPage=lambda canv, document: _draw_staff_page_header(
                    canv, document, schedule
                ),
            )
        ]
    )
    doc.build([grid])
    return buffer.getvalue()


def render_weekly_schedule_pdf(schedule: dict) -> bytes:
    """Render the given weekly schedule onto a single A4 landscape page."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN + HEADER_H,
        bottomMargin=MARGIN,
        title="Weekly Schedule {0}".format(schedule.get("range_heading") or "").strip(),
        author=COMPANY_NAME,
    )
    fitted = KeepInFrame(
        INNER_W,
        max(BODY_H, 40 * mm),
        _build_grid(schedule, scale=1.0),
        mode="shrink",
        hAlign="LEFT",
        vAlign="TOP",
        fakeWidth=False,
    )
    doc.build(
        [fitted],
        onFirstPage=lambda canv, document: _draw_page_header(canv, document, schedule),
        onLaterPages=lambda canv, document: _draw_page_header(canv, document, schedule),
    )
    return buffer.getvalue()
