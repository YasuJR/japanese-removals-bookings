"""PDF run sheet for a driver's daily jobs."""

import io
from typing import Any, Dict, List

import config
from display_dates import format_display_date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _date_label(move_date: str) -> str:
    parts = format_display_date(move_date)
    return "{0} {1}".format(parts["weekday"], parts["day_month"])


def generate_driver_run_sheet_pdf(
    crew_name: str,
    move_date: str,
    jobs: List[Dict[str, Any]],
    jobs_label: str,
    hours_label: str,
) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "RunTitle",
        parent=styles["Heading1"],
        fontSize=17,
        textColor=colors.HexColor("#1a2744"),
        spaceAfter=4,
    )
    sub_style = ParagraphStyle(
        "RunSub",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#5a6478"),
        spaceAfter=8,
    )
    job_title_style = ParagraphStyle(
        "JobTitle",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#1a2744"),
        spaceBefore=6,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "JobBody",
        parent=styles["Normal"],
        fontSize=10,
        leading=13,
    )

    story = []
    story.append(Paragraph(config.COMPANY_NAME, title_style))
    story.append(
        Paragraph(
            "Driver run sheet · {0} · {1}".format(
                crew_name, _date_label(move_date)
            ),
            sub_style,
        )
    )
    story.append(
        Paragraph(
            "{0} · {1}".format(jobs_label, hours_label),
            sub_style,
        )
    )
    story.append(Spacer(1, 4 * mm))

    if not jobs:
        story.append(Paragraph("No jobs assigned for this day.", body_style))
    else:
        for index, job in enumerate(jobs, start=1):
            story.append(
                Paragraph(
                    "{0}. {1} — {2}".format(
                        index,
                        job.get("start_display") or "—",
                        job.get("customer_name") or "—",
                    ),
                    job_title_style,
                )
            )
            suburb = job.get("suburb_badge") or ""
            status = job.get("status") or ""
            meta = []
            if suburb:
                meta.append(suburb)
            if status:
                meta.append(status)
            if meta:
                story.append(Paragraph(" · ".join(meta), sub_style))

            rows = [
                ["Phone", job.get("phone") or "—"],
                ["Pickup", job.get("pickup_address") or "—"],
                ["Delivery", job.get("delivery_address") or "—"],
            ]
            notes = job.get("notes") or ""
            if notes:
                rows.append(["Notes", notes])

            table = Table(rows, colWidths=[28 * mm, 145 * mm])
            table.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8d2c8")),
                        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f7f4ef")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, 3 * mm))

    footer = "Generated for crew use. Timezone: {0}".format(config.TIMEZONE)
    if config.COMPANY_PHONE:
        footer = "Office: {0} · ".format(config.COMPANY_PHONE) + footer
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(footer, sub_style))

    doc.build(story)
    return buffer.getvalue()
