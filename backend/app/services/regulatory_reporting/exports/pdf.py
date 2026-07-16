"""PDF rendering of a resolved return (docs/regulatory_reporting.md §5).

reportlab, A4: cover page (institution, return title, reporting date, basis,
directive citation, honest fidelity grade, SANDBOX watermark only when the
return's default channel is the ORASS sandbox simulator), attestation /
signature block page, one grid table per section with the GHS '000 note, and
a provenance appendix listing every source run and input hash.

Styling stays regulator-neutral: a single navy header rule, grey table grids,
no invented branding. Canvases are built ``invariant`` so identical content
always produces identical bytes (stable re-export checksums).
"""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.regulatory_reporting.templates import (
    RenderedReturn,
    RenderedSection,
    format_cell,
)

_NAVY = colors.HexColor("#1F3864")  # single header rule; no other branding
_GRID_GREY = colors.HexColor("#BFBFBF")
_HEADER_GREY = colors.HexColor("#D9D9D9")
_TOTAL_GREY = colors.HexColor("#F2F2F2")
_WATERMARK_GREY = colors.Color(0.75, 0.75, 0.75, alpha=0.4)

_STYLES = getSampleStyleSheet()
_TITLE = ParagraphStyle("ReturnTitle", parent=_STYLES["Title"], textColor=_NAVY)
_H2 = ParagraphStyle("SectionTitle", parent=_STYLES["Heading2"], textColor=_NAVY)
_BODY = _STYLES["BodyText"]
_SMALL = ParagraphStyle("Small", parent=_STYLES["BodyText"], fontSize=8, leading=10)
_CELL = ParagraphStyle("Cell", parent=_STYLES["BodyText"], fontSize=8, leading=10)
_CELL_RIGHT = ParagraphStyle("CellRight", parent=_CELL, alignment=2)


def _invariant_canvas(*args: Any, **kwargs: Any) -> pdf_canvas.Canvas:
    kwargs["invariant"] = 1
    return pdf_canvas.Canvas(*args, **kwargs)


class _PageFurniture:
    """Draws the navy header rule on every page and the SANDBOX watermark
    when the package's default submission channel is the sandbox simulator."""

    def __init__(self, *, watermark: bool, footer: str) -> None:
        self._watermark = watermark
        self._footer = footer

    def __call__(self, canvas: pdf_canvas.Canvas, _doc: SimpleDocTemplate) -> None:
        width, height = A4
        canvas.saveState()
        canvas.setStrokeColor(_NAVY)
        canvas.setLineWidth(2)
        canvas.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(18 * mm, 10 * mm, self._footer)
        canvas.drawRightString(width - 18 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        if self._watermark:
            canvas.setFont("Helvetica-Bold", 72)
            canvas.setFillColor(_WATERMARK_GREY)
            canvas.translate(width / 2, height / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, "SANDBOX")
        canvas.restoreState()


def _cover(rendered: RenderedReturn) -> list[Any]:
    pairs = dict(rendered.metadata_pairs)
    story: list[Any] = [
        Spacer(0, 30 * mm),
        Paragraph(pairs.get("Return", rendered.template.title), _TITLE),
        Spacer(0, 6 * mm),
    ]
    cover_fields = (
        "Institution",
        "Institution code",
        "Reporting date",
        "Reporting period",
        "Reporting basis",
        "Currency unit",
        "Sign convention",
        "Directive citation",
        "Template fidelity",
        "Template id",
        "Package version",
        "Generated at",
    )
    rows = [
        [Paragraph(f"<b>{label}</b>", _CELL), Paragraph(pairs.get(label, ""), _CELL)]
        for label in cover_fields
    ]
    table = Table(rows, colWidths=[42 * mm, 120 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, _GRID_GREY),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(table)
    if rendered.template.notes:
        story.append(Spacer(0, 6 * mm))
        for note in rendered.template.notes:
            story.append(Paragraph(f"Note: {note}", _SMALL))
    return story


def _attestation(rendered: RenderedReturn) -> list[Any]:
    story: list[Any] = [
        PageBreak(),
        Paragraph("Attestation", _H2),
        Spacer(0, 4 * mm),
    ]
    for line in rendered.attestation_lines:
        story.append(Paragraph(line, _BODY))
        if line.endswith(": "):
            story.append(Spacer(0, 2 * mm))
            underscore = Table([[""]], colWidths=[150 * mm], rowHeights=[8 * mm])
            underscore.setStyle(TableStyle([("LINEBELOW", (0, 0), (0, 0), 0.75, colors.black)]))
            story.append(underscore)
        story.append(Spacer(0, 4 * mm))
    return story


def _section_table(section: RenderedSection) -> Table:
    header = [Paragraph(f"<b>{column.header}</b>", _CELL) for column in section.layout.columns]
    body: list[list[Any]] = [header]
    numeric_kinds = ("ghs", "pct", "number", "auto")
    for rendered_row in [*section.rows, *([section.total_row] if section.total_row else [])]:
        cells = []
        for spec, cell in zip(section.layout.columns, rendered_row.cells, strict=True):
            style = _CELL_RIGHT if spec.kind in numeric_kinds else _CELL
            cells.append(Paragraph(format_cell(cell), style))
        body.append(cells)
    table = Table(body, repeatRows=1)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, _GRID_GREY),
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_GREY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if section.total_row is not None:
        style.append(("BACKGROUND", (0, -1), (-1, -1), _TOTAL_GREY))
        style.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    table.setStyle(TableStyle(style))
    return table


def _sections(rendered: RenderedReturn) -> list[Any]:
    story: list[Any] = []
    for section in rendered.sections:
        story.append(PageBreak())
        story.append(Paragraph(section.title, _H2))
        story.append(
            Paragraph(
                f"{rendered.template.currency_unit} · Fidelity: {section.layout.fidelity} · "
                f"Layout: {section.layout.layout_id}",
                _SMALL,
            )
        )
        story.append(Paragraph(f"Source: {section.layout.source_citation}", _SMALL))
        story.append(Spacer(0, 3 * mm))
        story.append(_section_table(section))
        for note in section.layout.notes:
            story.append(Spacer(0, 2 * mm))
            story.append(Paragraph(f"Note: {note}", _SMALL))
    return story


def _provenance(rendered: RenderedReturn) -> list[Any]:
    story: list[Any] = [PageBreak(), Paragraph("Provenance Appendix", _H2)]
    for line in rendered.provenance_lines:
        story.append(Paragraph(line, _SMALL))
    story.append(Spacer(0, 3 * mm))
    header = ["Module", "Run ID", "Input Hash", "Engine Version"]
    body: list[list[Any]] = [[Paragraph(f"<b>{item}</b>", _CELL) for item in header]]
    for module, run_id, input_hash, engine_version in rendered.provenance_runs:
        body.append(
            [
                Paragraph(module, _CELL),
                Paragraph(run_id, _CELL),
                Paragraph(input_hash, _CELL),
                Paragraph(engine_version, _CELL),
            ]
        )
    table = Table(body, colWidths=[20 * mm, 52 * mm, 62 * mm, 34 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, _GRID_GREY),
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_GREY),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(0, 4 * mm))
    story.append(Paragraph("Per-section fidelity", _SMALL))
    for section in rendered.sections:
        story.append(
            Paragraph(
                f"{section.layout.layout_id} — {section.layout.fidelity} — "
                f"{section.layout.source_citation}",
                _SMALL,
            )
        )
    return story


def render_pdf(rendered: RenderedReturn, *, sandbox_watermark: bool) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=16 * mm,
        title=rendered.template.title,
        author="AequorOS Regulatory Reporting",
    )
    furniture = _PageFurniture(watermark=sandbox_watermark, footer=rendered.provenance_lines[0])
    story = [
        *_cover(rendered),
        *_attestation(rendered),
        *_sections(rendered),
        *_provenance(rendered),
    ]
    document.build(
        story,
        onFirstPage=furniture,
        onLaterPages=furniture,
        canvasmaker=_invariant_canvas,
    )
    return buffer.getvalue()


__all__ = ["render_pdf"]
