"""Branded customer invoice PDFs (layout only — uses existing invoice math)."""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from crew import display_crew
from extra_charges import charge_line_total
from integrations import company_config, xero_branding
from integrations import stripe as stripe_service
import invoice

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Production palette (v5)
JR_PRIMARY = colors.HexColor("#083d28")
JR_SECONDARY = colors.HexColor("#dff2e7")
JR_TEXT = colors.HexColor("#222222")
JR_WHITE = colors.white
JR_GREEN_TABLE = JR_PRIMARY
JR_GREEN_LIGHT = JR_SECONDARY
JR_BORDER_SUBTLE = JR_SECONDARY

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_H = 6 * mm
MARGIN_V = 14 * mm
CONTENT_WIDTH = PAGE_WIDTH - (2 * MARGIN_H)
LOGO_WIDTH = 65 * mm
LOGO_GAP = 4 * mm
SECTION_GAP = 6 * mm
BILL_GAP = 7 * mm
TOTAL_GAP = 10 * mm
TOTALS_TOP_GAP = 8 * mm
SUBTOTAL_BLOCK_W = 98 * mm
FOOTER_TAGLINE = "Trusted Service | Smart Solutions | Stress-Free Relocations"


def _website_display(settings: Dict[str, Any]) -> str:
    site = str(settings.get("company_website") or "").strip()
    for prefix in ("https://", "http://", "www."):
        if site.lower().startswith(prefix):
            site = site[len(prefix) :]
    return site.strip("/")


def _contact_line(settings: Dict[str, Any]) -> str:
    parts: List[str] = []
    phone = str(settings.get("company_phone") or "").strip()
    email = str(settings.get("company_email") or "").strip()
    website = _website_display(settings)
    if phone:
        parts.append("Phone: {0}".format(phone))
    if email:
        parts.append("Email: {0}".format(email))
    if website:
        parts.append("Website: {0}".format(website))
    return " | ".join(parts)


def _issue_and_due_dates(booking: Dict[str, Any]) -> tuple:
    issue = (booking.get("invoice_issue_date") or booking.get("move_date") or "").strip()
    due = (booking.get("invoice_due_date") or "").strip()
    if issue and not due:
        try:
            due = (
                datetime.strptime(issue, "%Y-%m-%d").date()
                + timedelta(days=config.INVOICE_DUE_DAYS)
            ).isoformat()
        except ValueError:
            due = issue
    return issue, due


def _format_display_date(iso_date: str) -> str:
    if not iso_date:
        return "—"
    try:
        return datetime.strptime(iso_date[:10], "%Y-%m-%d").strftime("%d %B %Y")
    except ValueError:
        return iso_date


def _bank_details(booking: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, str]:
    account_name = (
        (booking.get("invoice_bank_account_name") or "").strip()
        or settings.get("bank_account_name")
        or ""
    )
    bsb = (booking.get("invoice_bank_bsb") or "").strip() or settings.get("bank_bsb") or ""
    account = (
        (booking.get("invoice_bank_account") or "").strip()
        or settings.get("bank_account_number")
        or ""
    )
    bank_name = str(settings.get("bank_name") or "Westpac").strip()
    return {
        "bank_name": bank_name,
        "account_name": str(account_name),
        "bsb": str(bsb),
        "account_number": str(account),
    }


def _labour_description(booking: Dict[str, Any], totals: Dict[str, Any]) -> str:
    hours = totals["hours"]
    rate = invoice.format_aud(totals["hourly_rate"])
    crew = display_crew(booking)
    return "<br/>".join(
        [
            "Moving Labour",
            "{0:.1f} hrs".format(hours),
            "{0}/hr".format(rate),
            "Crew:",
            crew,
        ]
    )


def _line_items(booking: Dict[str, Any], totals: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = [
        {
            "description_html": _labour_description(booking, totals),
            "quantity": "{0:.2f}".format(totals["hours"]),
            "unit_price": invoice.format_aud(totals["hourly_rate"]),
            "amount": invoice.format_aud(totals["hourly_rate"] * totals["hours"]),
        }
    ]
    if totals["callout_fee"] > 0:
        items.append(
            {
                "description_html": "Callout fee",
                "quantity": "1.00",
                "unit_price": invoice.format_aud(totals["callout_fee"]),
                "amount": invoice.format_aud(totals["callout_fee"]),
            }
        )
    for charge in totals.get("extra_charges") or []:
        qty = float(charge.get("quantity") or 1)
        unit = float(charge.get("unit_price") or 0)
        items.append(
            {
                "description_html": (charge.get("description") or "Extra charge"),
                "quantity": "{0:.2f}".format(qty),
                "unit_price": invoice.format_aud(unit),
                "amount": invoice.format_aud(charge_line_total(charge)),
            }
        )
    return items


def build_invoice_document(booking: Dict[str, Any]) -> Dict[str, Any]:
    """Shared layout data for PDF output and HTML preview."""
    settings = company_config.get_settings()
    totals = invoice.calculate_invoice_totals(booking)
    issue_date, due_date = _issue_and_due_dates(booking)
    invoice_number = (booking.get("invoice_number") or "").strip() or "DRAFT"
    logo_file = xero_branding.invoice_logo_path(hires=True)
    if not logo_file.is_file():
        logo_file = xero_branding.invoice_logo_path()
    company_abn = str(settings.get("company_abn") or "").strip()

    return {
        "settings": settings,
        "booking": booking,
        "totals": totals,
        "company_name": settings.get("company_name") or config.COMPANY_NAME,
        "company_phone": settings.get("company_phone") or "",
        "company_email": settings.get("company_email") or "",
        "company_website": _website_display(settings),
        "company_contact_line": _contact_line(settings),
        "company_abn": company_abn or "—",
        "company_location": settings.get("company_location") or "",
        "customer_name": booking.get("customer_name") or "",
        "invoice_number": invoice_number,
        "reference": str(booking.get("id") or ""),
        "issue_date": _format_display_date(issue_date),
        "due_date": _format_display_date(due_date),
        "invoice_status": (booking.get("invoice_status") or "DRAFT").upper(),
        "line_items": _line_items(booking, totals),
        "bank": _bank_details(booking, settings),
        "logo_url": xero_branding.invoice_logo_url(),
        "logo_path": str(logo_file) if logo_file.is_file() else "",
        "show_company_name": False,
        "footer_tagline": FOOTER_TAGLINE,
        "gst_inclusive": bool(totals.get("gst_enabled")),
        "payment_options": stripe_service.payment_options_for_booking(
            booking, totals["total"]
        ),
    }


def _styles():
    base = getSampleStyleSheet()
    return {
        "company_name": ParagraphStyle(
            "CompanyName",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=28,
            leading=30,
            textColor=JR_PRIMARY,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "contact_line": ParagraphStyle(
            "ContactLine",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9,
            textColor=JR_TEXT,
            alignment=TA_LEFT,
        ),
        "invoice_title": ParagraphStyle(
            "InvoiceTitle",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=35,
            leading=36,
            textColor=JR_PRIMARY,
            alignment=TA_LEFT,
            spaceAfter=0,
            charSpace=1.2,
        ),
        "meta_label": ParagraphStyle(
            "MetaLabel",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=9.5,
            textColor=JR_PRIMARY,
            alignment=TA_RIGHT,
        ),
        "meta_value": ParagraphStyle(
            "MetaValue",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=9.5,
            textColor=JR_TEXT,
            alignment=TA_RIGHT,
        ),
        "section_heading": ParagraphStyle(
            "SectionHeading",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9,
            textColor=JR_PRIMARY,
            spaceBefore=0,
            spaceAfter=3,
            charSpace=0.8,
        ),
        "customer_name": ParagraphStyle(
            "CustomerName",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            textColor=JR_TEXT,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=12,
            textColor=JR_TEXT,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=12,
            textColor=JR_WHITE,
        ),
        "line_desc": ParagraphStyle(
            "LineDesc",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            textColor=JR_TEXT,
        ),
        "payment_title": ParagraphStyle(
            "PaymentTitle",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=JR_PRIMARY,
        ),
        "payment_label": ParagraphStyle(
            "PaymentLabel",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=15,
            textColor=JR_PRIMARY,
        ),
        "payment_value": ParagraphStyle(
            "PaymentValue",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=15,
            textColor=JR_TEXT,
        ),
        "footer": ParagraphStyle(
            "Footer",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=6.5,
            leading=8,
            textColor=JR_TEXT,
            alignment=TA_CENTER,
        ),
        "total_bar_label": ParagraphStyle(
            "TotalBarLabel",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=26,
            leading=28,
            textColor=JR_WHITE,
            alignment=TA_LEFT,
        ),
        "total_bar_amount": ParagraphStyle(
            "TotalBarAmount",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=26,
            leading=28,
            textColor=JR_WHITE,
            alignment=TA_RIGHT,
        ),
        "subtotal_label": ParagraphStyle(
            "SubtotalLabel",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10,
            textColor=JR_TEXT,
            alignment=TA_RIGHT,
        ),
        "subtotal_value": ParagraphStyle(
            "SubtotalValue",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10,
            textColor=JR_TEXT,
            alignment=TA_RIGHT,
        ),
    }


def _logo_flowable(logo_path: Optional[str]) -> Optional[Image]:
    if not logo_path or not Path(logo_path).is_file():
        return None
    img = Image(logo_path, mask="auto")
    aspect = img.imageHeight / float(img.imageWidth)
    img.drawWidth = LOGO_WIDTH
    img.drawHeight = LOGO_WIDTH * aspect
    img.hAlign = "LEFT"
    return img


def _meta_block(doc_data: Dict[str, Any], styles: Dict[str, ParagraphStyle]) -> Table:
    rows = [
        ("Invoice Date", doc_data["issue_date"]),
        ("Invoice Number", doc_data["invoice_number"]),
        ("Reference", doc_data["reference"]),
        ("ABN", doc_data["company_abn"]),
    ]
    meta_rows = [
        [
            Paragraph(label, styles["meta_label"]),
            Paragraph(value, styles["meta_value"]),
        ]
        for label, value in rows
    ]
    meta_table = Table(meta_rows, colWidths=[32 * mm, 38 * mm], hAlign="RIGHT")
    meta_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    return meta_table


def _payment_details_table(
    bank: Dict[str, str],
    styles: Dict[str, ParagraphStyle],
    box_width: float,
) -> Table:
    label_w = box_width * 0.42
    value_w = box_width - label_w
    payment_rows = [
        [Paragraph("PAYMENT DETAILS", styles["payment_title"]), ""],
        [
            Paragraph("<b>Bank Name:</b>", styles["payment_label"]),
            Paragraph(bank["bank_name"], styles["payment_value"]),
        ],
        [
            Paragraph("<b>Account Name:</b>", styles["payment_label"]),
            Paragraph(bank["account_name"], styles["payment_value"]),
        ],
        [
            Paragraph("<b>BSB:</b>", styles["payment_label"]),
            Paragraph(bank["bsb"], styles["payment_value"]),
        ],
        [
            Paragraph("<b>Account Number:</b>", styles["payment_label"]),
            Paragraph(bank["account_number"], styles["payment_value"]),
        ],
    ]
    payment_table = Table(payment_rows, colWidths=[label_w, value_w])
    payment_table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (1, 0)),
                ("BACKGROUND", (0, 0), (-1, -1), JR_GREEN_LIGHT),
                ("BOX", (0, 0), (-1, -1), 1, JR_GREEN_TABLE),
                ("LEFTPADDING", (0, 0), (-1, -1), 18),
                ("RIGHTPADDING", (0, 0), (-1, -1), 18),
                ("TOPPADDING", (0, 1), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (1, 0), 14),
                ("BOTTOMPADDING", (0, 0), (1, 0), 10),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return payment_table


def _payment_options_table(
    options: Dict[str, Any],
    styles: Dict[str, ParagraphStyle],
    box_width: float,
) -> Table:
    pct = options.get("surcharge_percent_display") or "0"
    rows = [
        [Paragraph("PAYMENT OPTIONS", styles["payment_title"]), ""],
        [
            Paragraph("<b>Bank Transfer</b>", styles["payment_label"]),
            Paragraph(
                "Total: {0}<br/>No processing fee".format(
                    options.get("bank_total_display", "")
                ),
                styles["payment_value"],
            ),
        ],
        [
            Paragraph("<b>Credit Card</b>", styles["payment_label"]),
            Paragraph(
                "Total: {0}<br/>Includes {1}% card processing fee".format(
                    options.get("card_total_display", ""),
                    pct,
                ),
                styles["payment_value"],
            ),
        ],
        [
            Paragraph(
                "<i>{0}</i>".format(options.get("compliance_note", "")),
                styles["payment_value"],
            ),
            "",
        ],
    ]
    label_w = box_width * 0.38
    value_w = box_width - label_w
    table = Table(rows, colWidths=[label_w, value_w])
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (1, 0)),
                ("SPAN", (0, 3), (1, 3)),
                ("BACKGROUND", (0, 0), (-1, -1), JR_GREEN_LIGHT),
                ("BOX", (0, 0), (-1, -1), 1, JR_GREEN_TABLE),
                ("LEFTPADDING", (0, 0), (-1, -1), 18),
                ("RIGHTPADDING", (0, 0), (-1, -1), 18),
                ("TOPPADDING", (0, 1), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def generate_invoice_pdf(booking: Dict[str, Any]) -> bytes:
    """Build a branded customer invoice PDF (A4 portrait)."""
    doc_data = build_invoice_document(booking)
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN_H,
        rightMargin=MARGIN_H,
        topMargin=MARGIN_V,
        bottomMargin=MARGIN_V,
    )

    story: List[Any] = []

    # --- Header: wide logo (includes company name) + contact line only ---
    logo = _logo_flowable(doc_data.get("logo_path"))
    contact_block = Paragraph(doc_data["company_contact_line"], styles["contact_line"])
    if logo:
        brand_row = Table(
            [[logo, contact_block]],
            colWidths=[LOGO_WIDTH + LOGO_GAP, CONTENT_WIDTH - LOGO_WIDTH - LOGO_GAP],
            hAlign="LEFT",
        )
        brand_row.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (0, 0), 0),
                    ("RIGHTPADDING", (0, 0), (0, 0), LOGO_GAP),
                    ("LEFTPADDING", (1, 0), (1, 0), 2 * mm),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story.append(brand_row)
    else:
        story.append(contact_block)

    story.append(Spacer(1, SECTION_GAP))
    story.append(HRFlowable(width="100%", thickness=0.5, color=JR_BORDER_SUBTLE, spaceAfter=SECTION_GAP))

    title_left = [
        Paragraph("TAX INVOICE", styles["invoice_title"]),
        Spacer(1, 2 * mm),
        HRFlowable(width=62 * mm, thickness=0.5, color=JR_PRIMARY, spaceAfter=0),
    ]
    title_row = Table(
        [
            [
                title_left,
                _meta_block(doc_data, styles),
            ]
        ],
        colWidths=[CONTENT_WIDTH * 0.52, CONTENT_WIDTH * 0.48],
        hAlign="LEFT",
    )
    title_row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(title_row)
    story.append(Spacer(1, SECTION_GAP))

    # --- Bill to ---
    story.append(Paragraph("BILL TO", styles["section_heading"]))
    story.append(Paragraph(doc_data["customer_name"], styles["customer_name"]))
    story.append(Spacer(1, BILL_GAP))

    # --- Line items (full content width) ---
    desc_w = CONTENT_WIDTH * 0.48
    qty_w = CONTENT_WIDTH * 0.12
    unit_w = CONTENT_WIDTH * 0.20
    amt_w = CONTENT_WIDTH - desc_w - qty_w - unit_w

    table_header = [
        Paragraph("Description", styles["table_header"]),
        Paragraph("Qty", styles["table_header"]),
        Paragraph("Unit price", styles["table_header"]),
        Paragraph("Amount", styles["table_header"]),
    ]
    rows = [table_header]
    for item in doc_data["line_items"]:
        rows.append(
            [
                Paragraph(item["description_html"], styles["line_desc"]),
                Paragraph(item["quantity"], styles["line_desc"]),
                Paragraph(item["unit_price"], styles["line_desc"]),
                Paragraph(item["amount"], styles["line_desc"]),
            ]
        )

    line_table = Table(
        rows,
        colWidths=[desc_w, qty_w, unit_w, amt_w],
        repeatRows=1,
    )
    line_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), JR_GREEN_TABLE),
                ("TEXTCOLOR", (0, 0), (-1, 0), JR_WHITE),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10.5),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("TOPPADDING", (0, 0), (-1, 0), 12),
                ("LINEBELOW", (0, 0), (-1, 0), 1, JR_GREEN_TABLE),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, JR_BORDER_SUBTLE),
                ("LINEBELOW", (0, -1), (-1, -1), 0.75, JR_GREEN_TABLE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [JR_WHITE, JR_GREEN_LIGHT]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 1), (-1, -1), 11),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 11),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(line_table)
    story.append(Spacer(1, TOTALS_TOP_GAP))
    story.append(HRFlowable(width="100%", thickness=0.5, color=JR_BORDER_SUBTLE, spaceAfter=TOTALS_TOP_GAP))

    # --- Totals (subtotal/GST right-aligned, then full-width TOTAL AUD bar) ---
    totals = doc_data["totals"]
    subtotal_rows: List[List[str]] = []
    if totals["gst_enabled"]:
        subtotal_rows.extend(
            [
                ["Subtotal (ex GST)", invoice.format_aud(totals["subtotal"])],
                ["GST (10%)", invoice.format_aud(totals["gst_amount"])],
            ]
        )

    if subtotal_rows:
        subtotal_flow = [
            [
                Paragraph(row[0], styles["subtotal_label"]),
                Paragraph(row[1], styles["subtotal_value"]),
            ]
            for row in subtotal_rows
        ]
        subtotal_table = Table(
            subtotal_flow,
            colWidths=[54 * mm, 44 * mm],
            hAlign="RIGHT",
        )
        subtotal_table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(subtotal_table)
        story.append(Spacer(1, TOTAL_GAP))

    total_label = "TOTAL AUD"
    if totals["gst_enabled"]:
        total_label = "TOTAL AUD (incl. GST)"
    total_bar = Table(
        [
            [
                Paragraph(total_label, styles["total_bar_label"]),
                Paragraph(invoice.format_aud(totals["total"]), styles["total_bar_amount"]),
            ]
        ],
        colWidths=[CONTENT_WIDTH * 0.62, CONTENT_WIDTH * 0.38],
        hAlign="LEFT",
    )
    total_bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), JR_GREEN_TABLE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 14),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ]
        )
    )
    story.append(total_bar)
    story.append(Spacer(1, SECTION_GAP))

    payment_width = SUBTOTAL_BLOCK_W
    options_table = _payment_options_table(
        doc_data.get("payment_options") or {},
        styles,
        payment_width,
    )
    payment_table = _payment_details_table(doc_data["bank"], styles, payment_width)
    payment_row = Table(
        [[options_table, payment_table]],
        colWidths=[payment_width, payment_width],
        hAlign="LEFT",
    )
    payment_row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 8),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(payment_row)
    story.append(Spacer(1, SECTION_GAP * 3))
    story.append(HRFlowable(width="100%", thickness=0.4, color=JR_BORDER_SUBTLE, spaceAfter=SECTION_GAP))
    story.append(Paragraph(doc_data["footer_tagline"], styles["footer"]))

    doc.build(story)
    return buffer.getvalue()
