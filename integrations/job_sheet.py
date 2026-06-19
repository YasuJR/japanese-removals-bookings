"""PDF job sheets for crew."""

import io
from typing import Any, Dict

import config
from booking_times import display_finish_time, display_start_time
from crew import display_crew
from extra_charges import charge_line_total
from integrations import company_config
import invoice

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def generate_job_sheet_pdf(booking: Dict[str, Any]) -> bytes:
    """Build a one-page job sheet PDF for a booking."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "JRTitle",
        parent=styles["Heading1"],
        fontSize=18,
        textColor=colors.HexColor("#1a2744"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "JRSub",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#5a6478"),
        spaceAfter=12,
    )

    settings = company_config.get_settings()
    totals = invoice.calculate_invoice_totals(booking)

    story = []
    story.append(Paragraph(settings["company_name"], title_style))
    story.append(
        Paragraph(
            "Phone: {0} · Email: {1}".format(
                settings["company_phone"], settings["company_email"]
            ),
            subtitle_style,
        )
    )
    story.append(
        Paragraph(
            "Job sheet · Perth, WA · Booking #{0}".format(booking["id"]),
            subtitle_style,
        )
    )

    rows = [
        ["Move date", booking["move_date"]],
        ["Start time", display_start_time(booking)],
        ["Finish time", display_finish_time(booking)],
        ["Customer", booking["customer_name"]],
        ["Phone", booking["phone"]],
        ["Email", booking["email"]],
        ["Pickup", booking["pickup_address"]],
        ["Delivery", booking["delivery_address"]],
        ["Crew", display_crew(booking)],
        ["Movers required", str(booking["num_movers"])],
        ["Hourly rate", invoice.format_aud(totals["hourly_rate"]) + "/hr"],
        ["Callout fee", invoice.format_aud(totals["callout_fee"])],
        ["Notes", booking.get("notes") or "—"],
    ]

    for charge in totals.get("extra_charges") or []:
        label = "{0} ({1} × {2})".format(
            charge.get("description", "Extra"),
            charge.get("quantity", 1),
            invoice.format_aud(float(charge.get("unit_price") or 0)),
        )
        rows.append([label, invoice.format_aud(charge_line_total(charge))])

    rows.append(["Invoice total", invoice.format_aud(totals["total"])])

    table = Table(rows, colWidths=[45 * mm, 120 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#1a2744")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8d2c8")),
                ("ROWBACKGROUNDS", (1, 0), (1, -1), [colors.white, colors.HexColor("#f7f4ef")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 12 * mm))

    footer = "Generated for crew use. "
    if settings.get("company_phone"):
        footer += "Office: {0} · ".format(settings["company_phone"])
    footer += "Timezone: {0}".format(config.TIMEZONE)
    story.append(Paragraph(footer, subtitle_style))

    doc.build(story)
    return buffer.getvalue()
