"""Compact A4 landscape weekly schedule PDF (read-only over existing bookings)."""

from __future__ import annotations

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
    KeepInFrame,
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


def _staff_header_extra(schedule: dict) -> float:
    extra = 0.0
    subtitle_lines = schedule.get("subtitle_lines") or []
    if subtitle_lines:
        extra += len(subtitle_lines) * 4.0 * mm
    elif schedule.get("subtitle"):
        extra += 4.0 * mm
    footer_count = 0
    if schedule.get("weekly_paid_display"):
        footer_count += 1
    footer_count += len(schedule.get("staff_paid_lines") or [])
    extra += footer_count * 3.6 * mm
    return extra


def _job_html_staff(job: dict) -> str:
    time_range = _esc(job.get("time_range") or "Time TBC")
    duration = _esc(job.get("duration_label") or "")
    time_line = time_range
    if duration:
        time_line = "{0}&nbsp;&nbsp;<b>{1}</b>".format(time_range, duration)

    name = _esc(job.get("customer_name") or "—")
    pickup = _esc(job.get("pickup_address") or "—")
    delivery = _esc(job.get("delivery_address") or "—")
    status = _esc(str(job.get("status") or "").strip())

    parts = [
        "<b>{0}</b>".format(time_line),
        "Job: <b>{0}</b>".format(name),
    ]
    if job.get("show_crew") and job.get("crew_display"):
        parts.append("Crew: {0}".format(_esc(job.get("crew_display") or "—")))
    parts.extend(
        [
            "P: {0}".format(pickup),
            "D: {0}".format(delivery),
        ]
    )
    if status:
        parts.append("Status: {0}".format(status))
    return "<br/>".join(parts)


def _day_body_staff_individual(day: dict, styles: dict[str, ParagraphStyle]) -> Paragraph:
    jobs = day.get("jobs") or []
    if not jobs:
        return Paragraph("NO JOBS", styles["empty"])
    blocks = [_job_html_staff(job) for job in jobs]
    html = "<br/><font color='#c5d0dc'>----------</font><br/>".join(blocks)
    paid = _esc(day.get("paid_display") or "0hr")
    html = "{0}<br/><br/><b>Daily Paid: {1}</b>".format(html, paid)
    return Paragraph(html, styles["job"])


def _day_body_staff_all(day: dict, styles: dict[str, ParagraphStyle]) -> Paragraph:
    blocks = day.get("staff_blocks") or []
    active = [block for block in blocks if block.get("jobs")]
    if not active:
        return Paragraph("NO JOBS", styles["empty"])
    parts: list[str] = []
    for block in active:
        parts.append("<b>{0}</b>".format(_esc(block.get("staff") or "—")))
        for job in block.get("jobs") or []:
            parts.append(_job_html_staff(job))
        parts.append(
            "Paid: <b>{0}</b>".format(_esc(block.get("paid_display") or "0hr"))
        )
        parts.append("<font color='#c5d0dc'>----------</font>")
    return Paragraph("<br/>".join(parts), styles["job"])


def _draw_staff_page_header(canvas, _doc, schedule: dict) -> None:
    footer_lines: list[str] = []
    if schedule.get("weekly_paid_display"):
        footer_lines.append(
            "Weekly Paid Hours: {0}".format(schedule.get("weekly_paid_display"))
        )
    for line in schedule.get("staff_paid_lines") or []:
        footer_lines.append(line)
    _draw_schedule_header(
        canvas,
        title="WEEKLY STAFF SCHEDULE",
        subtitle_lines=schedule.get("subtitle_lines")
        or ([schedule.get("subtitle")] if schedule.get("subtitle") else []),
        footer_lines=footer_lines,
    )


def render_staff_weekly_schedule_pdf(schedule: dict) -> bytes:
    """Render staff portal weekly schedule onto a single A4 landscape page."""
    mode = str(schedule.get("mode") or "individual")
    day_body_fn = (
        _day_body_staff_all if mode == "all" else _day_body_staff_individual
    )
    header_extra = _staff_header_extra(schedule)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN + HEADER_H + header_extra,
        bottomMargin=MARGIN,
        title="Staff Weekly Schedule {0}".format(schedule.get("range_heading") or "").strip(),
        author=COMPANY_NAME,
    )
    fitted = KeepInFrame(
        INNER_W,
        max(BODY_H - header_extra, 40 * mm),
        _build_grid(schedule, scale=1.0, day_body_fn=day_body_fn),
        mode="shrink",
        hAlign="LEFT",
        vAlign="TOP",
        fakeWidth=False,
    )
    doc.build(
        [fitted],
        onFirstPage=lambda canv, document: _draw_staff_page_header(canv, document, schedule),
        onLaterPages=lambda canv, document: _draw_staff_page_header(canv, document, schedule),
    )
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
