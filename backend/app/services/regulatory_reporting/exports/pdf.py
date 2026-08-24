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
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.regulatory_reporting.templates import (
    ColumnSpec,
    RenderedCell,
    RenderedReturn,
    RenderedRow,
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
_FIELD_LABEL = ParagraphStyle(
    "FieldLabel", parent=_CELL, fontSize=6.5, leading=8, textColor=colors.grey
)

#: The attestation block's four signing cells, in points, across the 18 mm text
#: margins (595.28 − 2 × 51.02 = 493.2 pt). The signature column is the widest
#: because it has to hold the whole signature stamp — its floor is
#: ``pdf_signing.MIN_BOX_SIZES['signature']`` and this leaves generous room above
#: it. The gutter is right-padding inside each cell, so a value never touches the
#: neighbouring rule.
_SIGNING_COLUMN_WIDTHS: tuple[float, ...] = (128, 128, 158, 79.2)
_SIGNING_COLUMN_GUTTER = 8

#: Clear vertical space above each rule. Sized for the signature stamp with room
#: to spare, so an operator never has to enlarge the default box; the other three
#: values are single lines and sit on the same rule.
_SIGNING_ROW_HEIGHT = 48
_SIGNING_LABEL_HEIGHT = 9
_SIGNING_BLOCK_LEAD = 2 * mm


#: MIXED ORIENTATION, and the split is load-bearing.
#:
#: The cover and attestation pages stay PORTRAIT because the e-signature fields
#: live on them: ``pdf_signing.ATTESTATION_PAGE_INDEX`` is page index 1, and
#: every placement's coordinates are portrait user space. Rotating those pages
#: puts the boxes outside the media box and ``pdf_signing`` refuses the document
#: — "The preparer signature box (51, 672, 171, 688) falls outside page 1, whose
#: media box is (0, 0, 841.89, 595.276)". That refusal is correct: a signature
#: the operator cannot see is not the signature they placed, and a deployment
#: that cannot sign cannot file. (Tried and reverted, 2026-08-23.)
#:
#: The SECTION pages rotate to landscape, because that is where the width is
#: actually needed: the LMTD appendix carries ten columns in Table 8 and seven in
#: Table 9, whose headers broke mid-word on portrait ("Concentratio n of deposit
#: funding", "> 12 m onths"). Landscape gives 297 mm against 210 — 41% more.
#:
#: The provenance appendix returns to portrait: it is prose and a four-column
#: table, and keeping the document's first and last pages the same shape is
#: what a filed return looks like.
#:
#: NOTE this is not the fix for over-long NUMBERS. Those came from money cells
#: printing at raw Decimal precision and are fixed at source in
#: ``templates.format_cell``; a wider page would only give a wrong number more
#: room.
_PORTRAIT = A4
_LANDSCAPE = landscape(A4)

#: Page template ids, referenced by the ``NextPageTemplate`` flowables in
#: :func:`render_pdf`.
_PORTRAIT_TEMPLATE = "portrait"
_LANDSCAPE_TEMPLATE = "landscape"

_MARGINS = {
    "leftMargin": 18 * mm,
    "rightMargin": 18 * mm,
    "topMargin": 20 * mm,
    "bottomMargin": 16 * mm,
}


def _canvas_pagesize(canvas: pdf_canvas.Canvas) -> tuple[float, float]:
    """This page's (width, height) in points, whatever template produced it."""
    size = getattr(canvas, "_pagesize", None)
    if size is None:
        return _PORTRAIT
    width, height = size
    return float(width), float(height)


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
        # The furniture draws on WHICHEVER template this page uses, so the size
        # is read from the canvas rather than a module constant — the header rule
        # and footer must span a landscape section page as well as a portrait
        # cover.
        width, height = _canvas_pagesize(canvas)
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


def _signing_block() -> Table:
    """The four cells one officer signs into: name, designation, signature, date.

    Replaces a single 150 mm rule that ran under the whole "(name / designation /
    signature / date)" wording. That rule asked for four things and gave one
    undivided line to put them on, so the default field placement had nowhere to
    land: the boxes ended up in the clear band *below* the block, and an operator
    had to drag every field onto a line the template had not drawn.

    The geometry is load-bearing, not cosmetic. ``pdf_signing.DEFAULT_PLACEMENTS``
    is pinned to these columns and to :data:`_SIGNING_ROW_HEIGHT`, and
    ``tests/services/test_attestation_pdf_signing.py`` measures the rendered page
    to prove the two still agree — so changing a width here without moving the
    placements fails a test rather than filing a return with a signature stamp
    beside its line instead of on it.
    """
    labels = ("Name", "Designation", "Signature", "Date")
    body = [
        ["", "", "", ""],
        [Paragraph(label, _FIELD_LABEL) for label in labels],
    ]
    table = Table(
        body,
        colWidths=list(_SIGNING_COLUMN_WIDTHS),
        rowHeights=[_SIGNING_ROW_HEIGHT, _SIGNING_LABEL_HEIGHT],
    )
    table.setStyle(
        TableStyle(
            [
                # One rule per cell, under the space the value is signed into.
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), _SIGNING_COLUMN_GUTTER),
                ("TOPPADDING", (0, 1), (-1, 1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _attestation(rendered: RenderedReturn) -> list[Any]:
    story: list[Any] = [
        PageBreak(),
        Paragraph("Attestation", _H2),
        Spacer(0, 4 * mm),
    ]
    for line in rendered.attestation_lines:
        story.append(Paragraph(line, _BODY))
        if line.endswith(": "):
            story.append(Spacer(0, _SIGNING_BLOCK_LEAD))
            story.append(_signing_block())
        story.append(Spacer(0, 4 * mm))
    return story


#: Column kinds whose cells are numbers: right-aligned, and never allowed to
#: wrap. A label reading over two lines is ordinary on a regulatory form; a
#: figure broken across two lines ("601,209." / "47") is a misread waiting to
#: happen, so numeric columns get their width before anything else does.
_NUMERIC_KINDS = ("ghs", "pct", "number", "auto")

#: Cell padding per side. reportlab defaults to 6pt, which on the 17-column
#: LMTD Table 2 grid spends 204pt — over a quarter of the landscape frame — on
#: whitespace the figures themselves need.
_CELL_PAD = 2.0

#: Section tables are set at body size and stepped down only as far as the grid
#: actually requires. The floor is the point at which a printed schedule stops
#: being readable; BoG's own workbooks print these appendices small, and the
#: alternative to a smaller point size is wrapping every figure.
_MAX_TABLE_FONT = 8.0
_MIN_TABLE_FONT = 5.5
_FONT_STEP = 0.25

#: Slack added to every floor. A column sized to EXACTLY its widest token loses
#: the tie — reportlab measures the line against the padded width with its own
#: rounding, and "off-balance-sheet" in a 59.9pt cell needing 59.9pt split as
#: "off-b / alance-sheet". A point costs nothing and settles it.
_FIT_SLACK = 1.0

#: A text column is widened to hold its longest token only up to this multiple
#: of the font size — roughly seventeen characters, which covers the longest
#: words these schedules actually use ("counterparties", "unencumbered"). Past
#: it the column stops growing and the token splits, which is what a snapshot
#: row code should do: widening a "#" column to fit
#: ``on_balance_mismatch_total_ghs`` whole is exactly what starved the thirteen
#: maturity buckets.
_WORD_FLOOR_EMS = 9.0


def _text_width(text: str, size: float, *, bold: bool = False) -> float:
    return stringWidth(text, "Helvetica-Bold" if bold else "Helvetica", size)


def _longest_word(text: str, size: float, *, bold: bool = False) -> float:
    """Width of the widest unbreakable token.

    reportlab wraps at whitespace ONLY — a hyphenated compound like
    "off-balance-sheet" is one token to it, not three. Below this width it stops
    wrapping and starts splitting inside the word, which is how a bucket header
    came out as "15 da ys to 1 mt h" and a label as "provided to o / ff-balance-
    sh / eet vehicles".
    """
    return max((_text_width(word, size, bold=bold) for word in text.split()), default=0.0)


def _section_cells(section: RenderedSection) -> list[RenderedRow]:
    return [*section.rows, *([section.total_row] if section.total_row else [])]


def _column_bounds(section: RenderedSection, size: float) -> tuple[list[float], list[float]]:
    """Per column: the width that fits everything on one line, and the floor
    below which its content starts breaking badly.

    For a numeric column the floor IS the widest figure — those must never
    wrap. For a text column it is the longest word, capped, so ordinary words
    stay whole while a long identifier is allowed to split.
    """
    rows = _section_cells(section)
    pad = 2 * _CELL_PAD
    desired: list[float] = []
    floor: list[float] = []
    for index, spec in enumerate(section.layout.columns):
        # Budget against what is PRINTED. Measuring the machine value would
        # reserve width for a string the page never shows.
        values = [_display_cell(spec, row.cells[index], is_total=row.is_total) for row in rows]
        header_width = _text_width(spec.header, size, bold=True)
        desired.append(
            pad + max([header_width, *(_text_width(value, size) for value in values)], default=0.0)
        )
        # A header word is never split: it is what identifies the column, and a
        # heading reading "Contractu al" is the defect this whole function
        # exists to remove. Only VALUE tokens are subject to the cap, because
        # only they can be arbitrarily long identifiers.
        header_word = _longest_word(spec.header, size, bold=True)
        if spec.kind in _NUMERIC_KINDS:
            # The figures must fit whole. Beyond that the header may wrap at its
            # spaces ("15 days to" / "1 mth"), which reads fine and costs
            # nothing, so its own words only claim width up to the cap.
            need = max([_text_width(value, size) for value in values], default=0.0)
            floor.append(pad + _FIT_SLACK + max(need, min(header_word, _WORD_FLOOR_EMS * size)))
        else:
            value_word = max([_longest_word(value, size) for value in values], default=0.0)
            floor.append(
                pad + _FIT_SLACK + max(header_word, min(value_word, _WORD_FLOOR_EMS * size))
            )
    return desired, floor


def _fit_font_size(section: RenderedSection, available: float) -> float:
    """Largest point size at which every column can hold its content.

    Sizing the type to the grid rather than the grid to the type is what keeps
    the numbers intact: seventeen columns of GHS figures do not fit A4 landscape
    at body size, and the choice is a smaller point size or split figures.
    """
    size = _MAX_TABLE_FONT
    while size > _MIN_TABLE_FONT:
        if sum(_column_bounds(section, size)[1]) <= available:
            return size
        size -= _FONT_STEP
    return _MIN_TABLE_FONT


def _column_widths(section: RenderedSection, available: float, size: float) -> list[float]:
    """Explicit column widths, because reportlab's own allocation mis-serves
    these grids badly.

    Left to itself, ``Table`` sizes each column to its widest content. In LMTD
    Table 2 the ``#`` column carries snapshot row codes as long as
    ``on_balance_mismatch_total_ghs``, so it took a huge share and starved the
    thirteen maturity buckets — whose headers then broke mid-word and whose
    figures wrapped mid-number.

    When the grid is over budget, text columns are squeezed toward their floors
    first and numeric columns last, because text wraps gracefully and numbers do
    not.
    """
    columns = section.layout.columns
    desired, floor = _column_bounds(section, size)

    total_desired = sum(desired)
    if total_desired <= available:
        # Fill the frame rather than leave the grid floating short of the right
        # margin. The slack goes to the text columns — the ones a reader gains
        # from seeing on fewer lines.
        text_indexes = {i for i, spec in enumerate(columns) if spec.kind not in _NUMERIC_KINDS}
        slack = available - total_desired
        if not text_indexes:
            return [width + slack / len(desired) for width in desired]
        share = slack / len(text_indexes)
        return [
            width + (share if index in text_indexes else 0.0) for index, width in enumerate(desired)
        ]

    widths = list(desired)
    over = total_desired - available
    for group in (
        [i for i, spec in enumerate(columns) if spec.kind not in _NUMERIC_KINDS],
        [i for i, spec in enumerate(columns) if spec.kind in _NUMERIC_KINDS],
    ):
        if over <= 0:
            break
        slack_by_index = {i: widths[i] - floor[i] for i in group if widths[i] > floor[i]}
        pool = sum(slack_by_index.values())
        if pool <= 0:
            continue
        taken = min(over, pool)
        for index, slack in slack_by_index.items():
            widths[index] -= taken * (slack / pool)
        over -= taken

    if over > 0:
        # Below the minimum point size even the floors do not fit. Scale
        # uniformly and accept the wrap — a table wider than its frame runs off
        # the paper, which loses the columns outright rather than compressing
        # them.
        widths = [width * (available / sum(widths)) for width in widths]
    return widths


def _display_cell(spec: ColumnSpec, cell: RenderedCell, *, is_total: bool) -> str:
    """What the FILED page shows for one cell.

    One deliberate departure from the machine value: a section total carries a
    snapshot key as its ``code`` — ``on_balance_mismatch_total_ghs`` — because
    that key is what ``_snapshot_total`` and the ``equals_sum_of_rows``
    validation bind to. Printed in LMTD Table 2's "#" column, beneath BoG's own
    row numbers 1 to 17, it reads as an internal variable leaking onto a return.
    It is also what forced that column wide enough to starve the thirteen
    maturity buckets, since it is ~15x the width of any other cell in it.

    A printed form does not number its total line, and the description column
    already says what the line is, so the cell is left empty. The key stays
    untouched in the snapshot and in the CSV/XLSX machine artifacts, which
    downstream consumers key on.
    """
    if is_total and spec.key == "code":
        return ""
    return format_cell(cell)


def _section_table(section: RenderedSection, available: float) -> Table:
    columns = section.layout.columns
    size = _fit_font_size(section, available)
    cell = ParagraphStyle("SectionCell", parent=_CELL, fontSize=size, leading=size + 2)
    cell_right = ParagraphStyle("SectionCellRight", parent=cell, alignment=2)
    body: list[list[Any]] = [[Paragraph(f"<b>{column.header}</b>", cell) for column in columns]]
    for rendered_row in _section_cells(section):
        body.append(
            [
                Paragraph(
                    _display_cell(spec, value, is_total=rendered_row.is_total),
                    cell_right if spec.kind in _NUMERIC_KINDS else cell,
                )
                for spec, value in zip(columns, rendered_row.cells, strict=True)
            ]
        )
    table = Table(body, colWidths=_column_widths(section, available, size), repeatRows=1)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, _GRID_GREY),
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_GREY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), _CELL_PAD),
        ("RIGHTPADDING", (0, 0), (-1, -1), _CELL_PAD),
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
        # Sections render on the LANDSCAPE template, so the budget is the
        # landscape frame's width — not the portrait one the cover uses.
        story.append(_section_table(section, _frame_width(_LANDSCAPE)))
        for note in section.layout.notes:
            story.append(Spacer(0, 2 * mm))
            story.append(Paragraph(f"Note: {note}", _SMALL))
    return story


def _provenance(rendered: RenderedReturn) -> list[Any]:
    story: list[Any] = [PageBreak(), Paragraph("Provenance Appendix", _H2)]
    for line in rendered.provenance_lines:
        story.append(Paragraph(line, _SMALL))
    story.append(Spacer(0, 3 * mm))
    # The run table is rendered ONLY when there are runs to show. A pack that
    # binds no calculation run by design — a master-data pack sealed by
    # ``register_state_digest`` — used to print the bare Module / Run ID / Input
    # Hash / Engine Version header over nothing, which reads as a failed render
    # on a filed document. The prose above already states why the pack has no
    # runs, so an empty grid adds no information and costs credibility.
    if rendered.provenance_runs:
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
    document = BaseDocTemplate(
        buffer,
        pagesize=_PORTRAIT,
        **_MARGINS,
        title=rendered.template.title,
        author="AequorOS Regulatory Reporting",
    )
    furniture = _PageFurniture(watermark=sandbox_watermark, footer=rendered.provenance_lines[0])
    document.addPageTemplates(
        [
            PageTemplate(
                id=_PORTRAIT_TEMPLATE,
                pagesize=_PORTRAIT,
                frames=[_frame(_PORTRAIT)],
                onPage=furniture,
            ),
            PageTemplate(
                id=_LANDSCAPE_TEMPLATE,
                pagesize=_LANDSCAPE,
                frames=[_frame(_LANDSCAPE)],
                onPage=furniture,
            ),
        ]
    )
    # Order matters and encodes the constraint above: the cover and attestation
    # render on the PORTRAIT template (signature fields depend on it), the
    # sections on LANDSCAPE, and the provenance appendix back on portrait. Each
    # ``NextPageTemplate`` takes effect at the following page break, which
    # ``_sections`` and ``_provenance`` each begin with.
    story = [
        *_cover(rendered),
        *_attestation(rendered),
        NextPageTemplate(_LANDSCAPE_TEMPLATE),
        *_sections(rendered),
        NextPageTemplate(_PORTRAIT_TEMPLATE),
        *_provenance(rendered),
    ]
    document.build(story, canvasmaker=_invariant_canvas)
    return buffer.getvalue()


def _frame_width(pagesize: tuple[float, float]) -> float:
    """Usable content width for a page size, after the shared margins."""
    return pagesize[0] - _MARGINS["leftMargin"] - _MARGINS["rightMargin"]


def _frame(pagesize: tuple[float, float]) -> Frame:
    """The single content frame for a page template, inset by the shared margins."""
    width, height = pagesize
    return Frame(
        _MARGINS["leftMargin"],
        _MARGINS["bottomMargin"],
        width - _MARGINS["leftMargin"] - _MARGINS["rightMargin"],
        height - _MARGINS["topMargin"] - _MARGINS["bottomMargin"],
        id="content",
    )


__all__ = ["render_pdf"]
