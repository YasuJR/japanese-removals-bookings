"""Furniture-list PDF (ReportLab) — attached by the website to the enquiry email.

Contents: list ID, rooms with item names, quantities and estimated m³ (per item and
per room), other items, total m³, notes, a "写真あり / has photos" line when photos
exist (photos themselves are not embedded), and a clear "estimate" statement.
"""

from __future__ import annotations

import io
from typing import Any, Dict, List
from xml.sax.saxutils import escape

import furniture_catalog as catalog

FONT = "HeiseiKakuGo-W5"
GREEN = "#0f3b2e"
YELLOW = "#fff3a3"
_font_ready = False


def _register_font() -> None:
    global _font_ready
    if _font_ready:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    _font_ready = True


def _m3(value: Any) -> str:
    number = float(value or 0)
    return "{0:.2f}".format(number).rstrip("0").rstrip(".") if number else "0"


def render_list_pdf(row: Dict[str, Any], photo_count: int) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _register_font()
    base = ParagraphStyle("base", fontName=FONT, fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=base, fontSize=8.5, leading=11.5, textColor=colors.HexColor("#4a5a54"))
    title = ParagraphStyle("title", parent=base, fontSize=17, leading=22, textColor=colors.HexColor(GREEN))
    h2 = ParagraphStyle("h2", parent=base, fontSize=11.5, leading=15, textColor=colors.HexColor(GREEN), spaceBefore=6)
    strong = ParagraphStyle("strong", parent=base, fontSize=12.5, leading=17)

    def p(text: Any, style=base) -> Paragraph:
        return Paragraph(escape(str(text if text is not None else "")).replace("\n", "<br/>"), style)

    changes = row.get("changes") or catalog.compare_to_baseline(row["content"], row.get("baseline"))
    summary = catalog.annotate_changes(catalog.summarize(row["content"]), changes)
    has_changes = bool(changes.get("active") and changes.get("count"))
    public_id = row["public_id"]
    story: List[Any] = [
        p("家具リスト / Furniture List", title),
        p("List ID: {0}".format(public_id), strong),
        Spacer(1, 2 * mm),
    ]
    meta = [
        ["Last updated / 最終更新", row.get("updated_display") or "—"],
        ["Items / 品目数", str(summary["item_count"])],
    ]
    contact = [row.get("contact_name"), row.get("contact_phone"), row.get("contact_email")]
    if any(contact):
        meta.append(["Contact (from list) / 連絡先", " · ".join(c for c in contact if c)])
    if row.get("contact_move_date"):
        meta.append(["Moving date / 引越し日", row["contact_move_date"]])
    meta_table = Table([[p(a, small), p(b)] for a, b in meta], colWidths=[48 * mm, 122 * mm])
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [meta_table, Spacer(1, 3 * mm)]

    estimate_line = "数量・体積は概算（推定値）です。 Quantities and volumes are approximate estimates, not a quote."
    story.append(p(estimate_line, small))
    if has_changes:
        legend = Table(
            [[p("黄色 = 初回提出後に変更（{0}件） / Yellow = changed after the first submission ({0})".format(changes["count"]))]],
            colWidths=[170 * mm],
        )
        legend.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(YELLOW)), ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#e3b341"))]))
        story += [Spacer(1, 2 * mm), legend]
    story.append(Spacer(1, 2 * mm))

    widths = [96 * mm, 16 * mm, 28 * mm, 30 * mm]
    header_style = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f1ed")),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#d5dfda")),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )
    head = [p("Item / 品目", small), p("Qty / 数量", small), p("m³ each / 1点", small), p("m³ / 小計", small)]

    if not summary["rooms"] and not summary["custom_items"]:
        story.append(p("No items entered / 品目の入力はありません。"))
    for room in summary["rooms"]:
        room_total = sum(line["m3"] for line in room["lines"])
        story.append(p("{0} — approx. {1} m³".format(room["label"], _m3(room_total)), h2))
        data = [head]
        marks = []
        for line in room["lines"]:
            label = line["label"]
            if line.get("changed"):
                label += "  (was × {0})".format(line["before_qty"]) if line.get("before_qty") else "  (added / 追加)"
                marks.append(len(data))
            data.append([p(label), p(line["qty"]), p(_m3(line["unit_m3"])), p(_m3(line["m3"]))])
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(header_style)
        for index in marks:
            table.setStyle(TableStyle([("BACKGROUND", (0, index), (-1, index), colors.HexColor(YELLOW))]))
        story.append(table)

    if summary["custom_items"]:
        story.append(p("Other items / その他の荷物", h2))
        data = [[p("Item / 品目", small), p("Qty / 数量", small), p("Size / 寸法", small), p("m³ / 小計", small)]]
        custom_marks = []
        for item in summary["custom_items"]:
            name = item["name"] + (" ({0})".format(item["room"]) if item["room"] else "")
            if item.get("changed"):
                name += "  (added / 追加)" if item["changed"] == "new" else "  (changed / 変更)"
                custom_marks.append(len(data))
            if item["note"]:
                name += "\n" + item["note"]
            data.append(
                [
                    p(name),
                    p(item["qty"]),
                    p(item["dims"] or "—"),
                    p(_m3(item["m3"]) if item["m3"] is not None else "寸法なし / no size"),
                ]
            )
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(header_style)
        for index in custom_marks:
            table.setStyle(TableStyle([("BACKGROUND", (0, index), (-1, index), colors.HexColor(YELLOW))]))
        story.append(table)
        if summary["unsized_custom_count"]:
            story.append(p("寸法のない荷物は合計m³に含まれていません。 Items without a size are not in the total.", small))

    removed = [(r["room"], r["label"], r["qty"]) for r in changes.get("removed") or []]
    removed += [("Other items", r["name"], r["qty"]) for r in changes.get("removed_custom") or []]
    if removed:
        story.append(p("Removed after first submission / 初回提出後に削除", h2))
        data = [[p("{0} · {1}".format(room, label)), p("was × {0}".format(qty))] for room, label, qty in removed]
        table = Table(data, colWidths=[140 * mm, 30 * mm])
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(YELLOW)), ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#e3b341"))]))
        story.append(table)

    story.append(Spacer(1, 4 * mm))
    total = Table(
        [[p("Total (estimate) / 合計（推定値）", strong), p("approx. {0} m³".format(_m3(summary["estimated_m3"])), strong)]],
        colWidths=[110 * mm, 60 * mm],
    )
    total.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor(GREEN)),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f7f4")),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(total)

    story.append(p("Photos / 写真", h2))
    if photo_count:
        story.append(
            p(
                "写真あり / has photos ({0}) — 写真は予約アプリの家具リスト {1} で確認できます "
                "(view them in the booking app, list {1}).".format(photo_count, public_id)
            )
        )
    else:
        story.append(p("写真なし / no photos"))

    story.append(p("Notes / メモ" + ("  (changed / 変更あり)" if changes.get("notes_changed") else ""), h2))
    notes = Table([[p(summary["notes"] or "—")]], colWidths=[170 * mm])
    if changes.get("notes_changed"):
        notes.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(YELLOW))]))
    story.append(notes)
    story.append(Spacer(1, 4 * mm))
    story.append(p(estimate_line + " List ID: {0}".format(public_id), small))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Furniture list {0}".format(public_id),
        author="Japanese Removals",
    )
    doc.build(story)
    return buf.getvalue()
