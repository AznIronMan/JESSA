from __future__ import annotations

import re
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _inline(text: str) -> str:
    safe = _escape(text)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)
    return safe


def markdown_to_pdf(title: str, content: str) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        rightMargin=0.62 * inch,
        leftMargin=0.62 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=title,
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="DocTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#17202A"),
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="DocHeading",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#0F766E"),
            spaceBefore=10,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            name="DocBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=12.5,
            textColor=colors.HexColor("#17202A"),
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="DocBullet",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.3,
            leading=12,
            leftIndent=10,
        )
    )

    story = [Paragraph(_inline(title), styles["DocTitle"])]
    bullet_items: list[ListItem] = []

    def flush_bullets() -> None:
        nonlocal bullet_items
        if bullet_items:
            story.append(ListFlowable(bullet_items, bulletType="bullet", start="bulletchar", leftIndent=14))
            story.append(Spacer(1, 4))
            bullet_items = []

    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            flush_bullets()
            story.append(Spacer(1, 4))
            continue
        if line.startswith("#"):
            flush_bullets()
            heading = line.lstrip("#").strip()
            if heading:
                story.append(Paragraph(_inline(heading), styles["DocHeading"]))
            continue
        if line.startswith(("- ", "* ")):
            bullet_items.append(ListItem(Paragraph(_inline(line[2:].strip()), styles["DocBullet"])))
            continue
        flush_bullets()
        story.append(Paragraph(_inline(line), styles["DocBody"]))
    flush_bullets()
    doc.build(story)
    return buffer.getvalue()
