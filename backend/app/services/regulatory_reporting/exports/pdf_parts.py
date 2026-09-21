"""Shared reportlab building blocks for the regulatory PDFs.

Seeded by ICAAP P0 (the first prose in any filed artifact: the Board-attested
stress narrative) and planned as the home of every helper the generic tabular
renderer (``exports/pdf.py``) and the ICAAP report renderer share. What lives
here today:

* the **invariant canvas** — every PDF is built with ``invariant=1`` so the same
  content always produces the same bytes (stable export checksums, and a
  signature over a re-export covers identical bytes);
* the **page furniture** — the single navy header rule, footer and optional
  SANDBOX watermark drawn on every page;
* the **prose flowables** — headed paragraphs for narrative text.

Prose safety: reportlab's ``Paragraph`` parses its input as a small markup
language (``<b>``, ``<font>``, ``<a href>``, entities). Narrative text is typed
by users, so it is NEVER passed to ``Paragraph`` raw: :func:`escape_text`
XML-escapes it first, which renders markup-looking input literally instead of
interpreting it (or crashing the build on an unbalanced tag). Line breaks the
author typed are preserved as ``<br/>``, which is the only markup this module
itself inserts.

Glyph coverage (deviation DV-004): the PDFs use reportlab's standard Helvetica,
which draws only the Windows-1252 repertoire. A character outside it (the cedi
sign, CJK, emoji) used to print as an unreadable black box; :func:`printable`
shows it as a plain ``?`` instead, and the narrative section's note says the
signed snapshot, the XLSX and the CSV hold the exact text. Embedding a Unicode
font is the P3 filing-PDF decision; it is not applied here so every existing
return's PDF stays byte-identical.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.services.regulatory_reporting.exports.text import xml_safe

#: The character repertoire of the standard PDF fonts (WinAnsiEncoding).
_STANDARD_FONT_ENCODING = "cp1252"
#: What a character the standard font cannot draw prints as.
UNPRINTABLE_SUBSTITUTE = "?"

NAVY = colors.HexColor("#1F3864")  # single header rule; no other branding
WATERMARK_GREY = colors.Color(0.75, 0.75, 0.75, alpha=0.4)


def canvas_pagesize(canvas: pdf_canvas.Canvas) -> tuple[float, float]:
    """This page's (width, height) in points, whatever template produced it."""
    size = getattr(canvas, "_pagesize", None)
    if size is None:
        return A4
    width, height = size
    return float(width), float(height)


def invariant_canvas(*args: Any, **kwargs: Any) -> pdf_canvas.Canvas:
    """A reportlab canvas with ``invariant=1``: no creation timestamp or random
    document id, so identical content yields byte-identical PDFs."""
    kwargs["invariant"] = 1
    return pdf_canvas.Canvas(*args, **kwargs)


class PageFurniture:
    """Draws the navy header rule on every page and the SANDBOX watermark
    when the package's default submission channel is the sandbox simulator."""

    def __init__(self, *, watermark: bool, footer: str) -> None:
        self._watermark = watermark
        self._footer = footer

    def __call__(self, canvas: pdf_canvas.Canvas, _doc: SimpleDocTemplate) -> None:
        # The furniture draws on WHICHEVER template this page uses, so the size
        # is read from the canvas rather than a module constant — the header rule
        # and footer must span a landscape section page as well as a portrait
        # cover.
        width, height = canvas_pagesize(canvas)
        canvas.saveState()
        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(2)
        canvas.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(18 * mm, 10 * mm, self._footer)
        canvas.drawRightString(width - 18 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        if self._watermark:
            canvas.setFont("Helvetica-Bold", 72)
            canvas.setFillColor(WATERMARK_GREY)
            canvas.translate(width / 2, height / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, "SANDBOX")
        canvas.restoreState()


def _drawable(character: str) -> bool:
    try:
        character.encode(_STANDARD_FONT_ENCODING)
    except UnicodeEncodeError:
        return False
    return True


def printable(text: str) -> str:
    """``text`` restricted to what the standard font can draw.

    Control characters are made safe first (``exports.text.xml_safe``: vertical
    tab / form feed become line breaks), then any character outside the font's
    repertoire becomes :data:`UNPRINTABLE_SUBSTITUTE` — visible, never a black
    box and never silently dropped.
    """
    text = xml_safe(text)
    if text.isascii():
        return text
    return "".join(ch if _drawable(ch) else UNPRINTABLE_SUBSTITUTE for ch in text)


def escape_text(text: str) -> str:
    """User text made safe for ``Paragraph``: printable, XML-escaped, line breaks kept.

    ``&``, ``<`` and ``>`` become entities (quotes need no escaping in element
    content), so ``<b>`` or ``<a href=…>`` typed into a narrative prints as those
    characters and is never interpreted. Line endings are normalised and each
    newline becomes ``<br/>``.
    """
    normalized = printable(text).replace("\r\n", "\n").replace("\r", "\n")
    return "<br/>".join(escape(line) for line in normalized.split("\n"))


def prose_paragraphs(text: str, style: ParagraphStyle) -> list[Paragraph]:
    """One ``Paragraph`` per author paragraph (blank-line separated), escaped."""
    normalized = xml_safe(text).replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block.strip("\n") for block in normalized.split("\n\n")]
    return [Paragraph(escape_text(block), style) for block in blocks if block.strip()]


def prose_entries(
    entries: Sequence[tuple[str, str]],
    *,
    label_style: ParagraphStyle,
    body_style: ParagraphStyle,
    gap: float = 3 * mm,
) -> list[Any]:
    """Headed paragraphs: each ``(label, text)`` pair as a bold label over its
    body. Both are escaped; callers substitute their own wording for an absent
    body (e.g. "Not stated") before calling, so nothing prints blank."""
    story: list[Any] = []
    for label, text in entries:
        story.append(Paragraph(f"<b>{escape_text(label)}</b>", label_style))
        story.extend(prose_paragraphs(text, body_style))
        story.append(Spacer(0, gap))
    return story


__all__ = [
    "NAVY",
    "WATERMARK_GREY",
    "PageFurniture",
    "canvas_pagesize",
    "UNPRINTABLE_SUBSTITUTE",
    "escape_text",
    "invariant_canvas",
    "printable",
    "prose_entries",
    "prose_paragraphs",
]
