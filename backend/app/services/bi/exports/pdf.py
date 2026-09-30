"""PDF rendering of a governed export: reportlab, landscape, byte-deterministic.

``docs/bi.md`` §Exports requires this artifact to be **deterministic**: identical
input must produce identical bytes. Three things would otherwise break that, and
all three are handled rather than hoped for:

* **the creation timestamp and the document id**, which reportlab writes into
  every PDF trailer. The canvas is built through
  ``regulatory_reporting.exports.pdf_parts.invariant_canvas`` (``invariant=1``),
  which is the same helper the filed returns use and the same reason they use it
  — a signature over a re-export has to cover identical bytes;
* **anything read from the clock**, which is why ``ExportContext`` holds no
  "generated at" and the watermark is attribution rather than time;
* **iteration order**, which is the compiler's column order throughout.

Layout: a cover page carrying the provenance block, then the grid over as many
landscape pages as it needs, with the table header repeated on each. The
watermark is drawn diagonally on every page by the page furniture, so a printed
or forwarded page still names the institution and the person who took it.

**Glyphs.** reportlab's standard Helvetica draws the Windows-1252 repertoire
only, so a character outside it (a cedi sign, CJK, an emoji in a counterparty
name) would print as a black box. ``pdf_parts.printable`` substitutes a visible
``?`` and the cover page says where the exact text is — the same deviation
(DV-004) and the same wording the regulatory PDFs carry.

**Width.** A ``BiQuery`` may name twenty-five measures over twelve dimensions,
which is more columns than a landscape page can hold legibly. Columns that do
not fit are NOT silently dropped: the page prints as many as fit and states, on
the cover and under the table, exactly which ones are only in the spreadsheet
and CSV copies. A number a reader cannot see is better than a number they
believe they are seeing.
"""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.services.bi.exports.context import (
    EXPORT_TITLE,
    STANDING_NOTE,
    ExportColumn,
    ExportContext,
    ExportTable,
    display_cell,
)
from app.services.regulatory_reporting.exports.pdf_parts import (
    NAVY,
    WATERMARK_GREY,
    canvas_pagesize,
    escape_text,
    invariant_canvas,
    printable,
)

MEDIA_TYPE = "application/pdf"
EXTENSION = "pdf"

_PAGE = landscape(A4)
_MARGIN = 14 * mm
_TOP_MARGIN = 20 * mm
_BOTTOM_MARGIN = 14 * mm

_GRID_GREY = colors.HexColor("#BFBFBF")
_HEADER_GREY = colors.HexColor("#D9D9D9")

_STYLES = getSampleStyleSheet()
_TITLE = ParagraphStyle("BiExportTitle", parent=_STYLES["Title"], textColor=NAVY, alignment=0)
_H2 = ParagraphStyle("BiExportHeading", parent=_STYLES["Heading2"], textColor=NAVY)
_BODY = ParagraphStyle("BiExportBody", parent=_STYLES["BodyText"], fontSize=9, leading=12)
_META_LABEL = ParagraphStyle("BiExportMetaLabel", parent=_BODY, fontName="Helvetica-Bold")
_CELL = ParagraphStyle("BiExportCell", parent=_STYLES["BodyText"], fontSize=7.5, leading=9)
_CELL_RIGHT = ParagraphStyle("BiExportCellRight", parent=_CELL, alignment=2)
_CELL_HEAD = ParagraphStyle("BiExportCellHead", parent=_CELL, fontName="Helvetica-Bold")

#: Narrowest a data column may be drawn, in points. Below this a heading wraps
#: to one character per line and the table stops being readable.
_MIN_COLUMN_WIDTH = 42.0
#: Widest a single column is allowed to grow before the rest are squeezed.
_MAX_COLUMN_WIDTH = 150.0
#: Rows per table flowable. Platypus splits a Table across pages itself, but it
#: builds the whole flowable first; chunking bounds peak memory on a large
#: export and lets the repeated header be re-stated per chunk.
_ROWS_PER_CHUNK = 250


class _Furniture:
    """The navy rule, the footer and the diagonal attribution watermark.

    Drawn on every page, from the context alone — nothing here reads the clock,
    which is what keeps the output byte-identical between renders.
    """

    def __init__(self, ctx: ExportContext) -> None:
        self._watermark = ctx.watermark
        self._footer = (
            f"{EXPORT_TITLE} · {ctx.institution_name} · as at {ctx.as_of_label} · "
            f"{STANDING_NOTE}"
        )

    def __call__(self, canvas: pdf_canvas.Canvas, _doc: BaseDocTemplate) -> None:
        width, height = canvas_pagesize(canvas)
        canvas.saveState()
        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(2)
        canvas.line(_MARGIN, height - 12 * mm, width - _MARGIN, height - 12 * mm)
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(colors.grey)
        canvas.drawString(_MARGIN, 8 * mm, printable(self._footer)[:220])
        canvas.drawRightString(width - _MARGIN, 8 * mm, f"Page {canvas.getPageNumber()}")
        canvas.setFont("Helvetica-Bold", 30)
        canvas.setFillColor(WATERMARK_GREY)
        canvas.translate(width / 2, height / 2)
        canvas.rotate(30)
        canvas.drawCentredString(0, 0, printable(self._watermark)[:90])
        canvas.restoreState()


def _document(buffer: io.BytesIO, ctx: ExportContext) -> BaseDocTemplate:
    document = BaseDocTemplate(
        buffer,
        pagesize=_PAGE,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_TOP_MARGIN,
        bottomMargin=_BOTTOM_MARGIN,
        title=f"{EXPORT_TITLE} — {ctx.institution_name}",
        author=EXPORT_TITLE,
        subject=STANDING_NOTE,
    )
    frame = Frame(
        document.leftMargin,
        document.bottomMargin,
        document.width,
        document.height,
        id="body",
    )
    document.addPageTemplates([PageTemplate(id="export", frames=[frame], onPage=_Furniture(ctx))])
    return document


def _natural_width(column: ExportColumn, values: list[str]) -> float:
    head = stringWidth(printable(column.label), "Helvetica-Bold", 7.5)
    widest = max((stringWidth(value, "Helvetica", 7.5) for value in values), default=0.0)
    return min(_MAX_COLUMN_WIDTH, max(_MIN_COLUMN_WIDTH, max(head, widest) + 10))


def _fit(table: ExportTable, available: float) -> tuple[int, list[float]]:
    """How many leading columns fit, and their widths scaled to the page.

    Columns are kept in the compiler's order and taken from the left, because
    that order puts the dimensions — what each row IS — before the figures.
    """

    text: list[list[str]] = [
        [display_cell(row[index], column) for row in table.rows[:_ROWS_PER_CHUNK]]
        for index, column in enumerate(table.columns)
    ]
    widths = [_natural_width(column, text[index]) for index, column in enumerate(table.columns)]
    kept: list[float] = []
    for width in widths:
        if kept and sum(kept) + width > available:
            break
        kept.append(width)
    if not kept:
        return 0, []
    scale = available / sum(kept)
    if scale < 1:
        kept = [width * scale for width in kept]
    return len(kept), kept


def _cell_paragraph(value: str, column: ExportColumn, *, heading: bool = False) -> Paragraph:
    if heading:
        return Paragraph(escape_text(value), _CELL_HEAD)
    return Paragraph(escape_text(value), _CELL_RIGHT if column.numeric else _CELL)


def _grid_style() -> TableStyle:
    return TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.25, _GRID_GREY),
            ("BACKGROUND", (0, 0), (-1, 0), _HEADER_GREY),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
    )


def _cover(ctx: ExportContext, table: ExportTable, omitted: list[str]) -> list[Any]:
    story: list[Any] = [
        Paragraph(escape_text(EXPORT_TITLE), _TITLE),
        Paragraph(escape_text(ctx.institution_name), _H2),
        Spacer(0, 4 * mm),
    ]
    rows = [
        [Paragraph(escape_text(field), _META_LABEL), Paragraph(escape_text(value), _BODY)]
        for field, value in ctx.metadata_rows()
    ]
    rows.append(
        [Paragraph("Rows", _META_LABEL), Paragraph(escape_text(f"{table.row_count:,}"), _BODY)]
    )
    if table.truncated:
        rows.append(
            [
                Paragraph("Completeness", _META_LABEL),
                Paragraph(
                    escape_text(
                        "Truncated at the export row cap. Narrow the window or add a "
                        "filter to export the remainder."
                    ),
                    _BODY,
                ),
            ]
        )
    if omitted:
        rows.append(
            [
                Paragraph("Columns not shown", _META_LABEL),
                Paragraph(
                    escape_text(
                        f"{', '.join(omitted)} — too wide for the page. "
                        "The spreadsheet and CSV copies carry every column."
                    ),
                    _BODY,
                ),
            ]
        )
    rows.append(
        [
            Paragraph("Characters", _META_LABEL),
            Paragraph(
                escape_text(
                    "A character this typeface cannot draw prints as '?'. "
                    "The spreadsheet and CSV copies carry the exact text."
                ),
                _BODY,
            ),
        ]
    )
    metadata = Table(rows, colWidths=[36 * mm, None], hAlign="LEFT")
    metadata.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, _GRID_GREY),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(metadata)
    return story


def _grid(table: ExportTable, kept: int, widths: list[float]) -> list[Any]:
    columns = table.columns[:kept]
    header = [_cell_paragraph(column.label, column, heading=True) for column in columns]
    story: list[Any] = []
    if not table.rows:
        empty = Table([header, [Paragraph("No rows", _CELL)] + [""] * (kept - 1)], colWidths=widths)
        empty.setStyle(_grid_style())
        return [empty]
    for start in range(0, len(table.rows), _ROWS_PER_CHUNK):
        chunk = table.rows[start : start + _ROWS_PER_CHUNK]
        data = [header]
        data.extend(
            [
                _cell_paragraph(display_cell(row[index], column), column)
                for index, column in enumerate(columns)
            ]
            for row in chunk
        )
        flowable = Table(data, colWidths=widths, repeatRows=1)
        flowable.setStyle(_grid_style())
        story.append(flowable)
    return story


def render_pdf(table: ExportTable, ctx: ExportContext) -> bytes:
    """The document. Identical input produces byte-identical output."""

    buffer = io.BytesIO()
    document = _document(buffer, ctx)
    kept, widths = _fit(table, document.width)
    omitted = [column.label for column in table.columns[kept:]]
    story = _cover(ctx, table, omitted)
    if kept:
        story.append(PageBreak())
        story.append(Paragraph(escape_text("Data"), _H2))
        story.append(Spacer(0, 2 * mm))
        story.extend(_grid(table, kept, widths))
    document.build(story, canvasmaker=invariant_canvas)
    return buffer.getvalue()


__all__ = ["EXTENSION", "MEDIA_TYPE", "render_pdf"]
