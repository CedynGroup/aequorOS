"""The ICAAP draft PDF (reportlab platypus).

What this renderer is for: an author, a reviewer or a Board member wants to
read the ICAAP as a document rather than as seventeen editor panes. That is the
only claim it makes. It is not a filing — the filed instrument is P3's signed
PDF built from the frozen snapshot — and every page says so.

Three properties are load-bearing rather than cosmetic:

* **It says what it is.** ``DRAFT — not approved`` is drawn diagonally across
  every page, and a rehearsal cycle adds ``REHEARSAL — not a regulatory
  filing`` beneath it (D-029). A rehearsal walks the whole lifecycle, so the
  watermark is the only thing separating its output from the real one.
* **Author text is never markup.** reportlab's ``Paragraph`` parses a small
  markup language, so every run of user text goes through
  :func:`~app.services.regulatory_reporting.exports.pdf_parts.escape_text`
  first and the only tags in the document are the ones this module writes
  around already-escaped text. A section containing ``<b>x</b> & <script>``
  prints those characters.
* **It is reproducible.** The canvas is built ``invariant`` (no creation
  timestamp, no random document id) and the one wall-clock value —
  ``generated_at`` — is supplied by the caller, so rendering the same document
  twice yields the same bytes.

Glyph coverage (deviation DV-004): the draft uses the standard Helvetica, whose
repertoire is Windows-1252. A character outside it — the cedi sign, Ghanaian
vowels such as Ɛ and Ɔ, CJK — is shown as a visible ``?`` and *counted*, and
the provenance page states the count and says the exact text is held in the
record. No page claims the text is reproduced exactly. P3's filing PDF embeds a
Unicode font (D-022) and passes its own :class:`FontSet` here; the default
keeps draft bytes unchanged.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.domain.icaap.document import (
    DRAFT_PROVENANCE_NOTICE,
    EXPOSURE_DRAFT_NOTICE,
    NOT_AVAILABLE,
    BlockRender,
    DataBlockRef,
    IcaapDocument,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    RenderBlock,
    Run,
    SectionRender,
    TableSpec,
)
from app.services.icaap.render import format as fmt
from app.services.regulatory_reporting.exports import pdf_parts
from app.services.regulatory_reporting.exports.text import xml_safe

_GRID_GREY = colors.HexColor("#BFBFBF")
_HEADER_GREY = colors.HexColor("#D9D9D9")
_TOTAL_GREY = colors.HexColor("#F2F2F2")

_PAGE = A4
_MARGINS = {
    "leftMargin": 18 * mm,
    "rightMargin": 18 * mm,
    "topMargin": 20 * mm,
    "bottomMargin": 16 * mm,
}
_TEMPLATE_ID = "icaap-draft"
#: The watermark never spans more than this share of the room available to it.
_WATERMARK_FILL = 0.82
_WATERMARK_MAX_SIZE = 60.0
_WATERMARK_MIN_SIZE = 10.0


@dataclass(frozen=True)
class FontSet:
    """The four faces a document is set in, and what that font can draw.

    P3's filing PDF registers an embedded Unicode family (D-022) and passes it
    here; ``printable`` is then its own coverage test rather than Helvetica's
    Windows-1252 one, so the substitution count on the provenance page stays
    truthful for whichever font actually rendered the page.
    """

    #: reportlab resolves ``<b>``/``<i>`` through the registered font FAMILY,
    #: not through these names, so an embedded set must be registered with
    #: ``registerFontFamily`` as well as ``registerFont``. The four names are
    #: here because the styles and the page furniture set faces directly.
    normal: str
    bold: str
    italic: str
    bold_italic: str
    printable: Callable[[str], str]


#: The base-14 default. Drafts use it so they need no font file at all.
HELVETICA = FontSet(
    normal="Helvetica",
    bold="Helvetica-Bold",
    italic="Helvetica-Oblique",
    bold_italic="Helvetica-BoldOblique",
    printable=pdf_parts.printable,
)


def _styles(fonts: FontSet) -> dict[str, ParagraphStyle]:
    sheet = getSampleStyleSheet()
    body = sheet["BodyText"]
    base = ParagraphStyle(
        "IcaapBody",
        parent=body,
        fontName=fonts.normal,
        fontSize=9.5,
        leading=13,
        spaceAfter=4,
    )
    return {
        "title": ParagraphStyle(
            "IcaapTitle",
            parent=sheet["Title"],
            fontName=fonts.bold,
            textColor=pdf_parts.NAVY,
        ),
        "h1": ParagraphStyle(
            "IcaapH1",
            parent=base,
            fontName=fonts.bold,
            fontSize=14,
            leading=18,
            spaceBefore=6,
            spaceAfter=4,
            textColor=pdf_parts.NAVY,
        ),
        "h2": ParagraphStyle(
            "IcaapH2", parent=base, fontName=fonts.bold, fontSize=12, leading=15, spaceBefore=6
        ),
        "h3": ParagraphStyle(
            "IcaapH3", parent=base, fontName=fonts.bold, fontSize=10.5, leading=14, spaceBefore=5
        ),
        "h4": ParagraphStyle(
            "IcaapH4", parent=base, fontName=fonts.bold, fontSize=9.5, leading=13, spaceBefore=4
        ),
        "body": base,
        "quote": ParagraphStyle(
            "IcaapQuote",
            parent=base,
            leftIndent=8 * mm,
            rightIndent=4 * mm,
            textColor=colors.HexColor("#333333"),
        ),
        "small": ParagraphStyle("IcaapSmall", parent=base, fontSize=8, leading=10.5, spaceAfter=2),
        "note": ParagraphStyle(
            "IcaapNote", parent=base, fontSize=8, leading=10.5, textColor=colors.grey
        ),
        "cell": ParagraphStyle("IcaapCell", parent=base, fontSize=8, leading=10, spaceAfter=0),
        "cell_right": ParagraphStyle(
            "IcaapCellRight",
            parent=base,
            fontSize=8,
            leading=10,
            spaceAfter=0,
            alignment=2,
        ),
        "cell_head": ParagraphStyle(
            "IcaapCellHead",
            parent=base,
            fontName=fonts.bold,
            fontSize=8,
            leading=10,
            spaceAfter=0,
        ),
    }


def _fit_font_size(text: str, font: str, *, max_width: float, max_size: float) -> float:
    """The largest size at or below ``max_size`` that keeps ``text`` inside
    ``max_width``. Deterministic: it reads the font metrics, nothing else."""
    width = stringWidth(text, font, max_size)
    if width <= max_width or width <= 0:
        return max_size
    return max(_WATERMARK_MIN_SIZE, max_size * max_width / width)


def _diagonal_span(width: float, height: float, *, offset: float) -> float:
    """How long a 45° line can be inside the page at ``offset`` from its centre.

    The page diagonal is the wrong bound and clipped the longer watermark off
    the right edge: a line drawn at 45° leaves the page at the short sides, and
    a second line stacked away from the centre has less room still. Solving the
    rectangle's two constraints for the line's parameter gives the exact chord,
    so a long watermark shrinks to fit instead of running off the paper.
    """
    cos45 = 2**-0.5
    reach_w = width / (2 * cos45)
    reach_h = height / (2 * cos45)
    upper = min(offset + reach_w, -offset + reach_h)
    lower = max(offset - reach_w, -offset - reach_h)
    return max(0.0, upper - lower)


class DraftFurniture:
    """The navy rule, the footer and the diagonal watermarks, on every page.

    P0's ``pdf_parts.PageFurniture`` draws a fixed ``SANDBOX`` watermark for the
    tabular returns; this is the ICAAP equivalent and takes its wording from the
    document, so a rehearsal cycle's pages carry both lines (D-029). P0's class
    is reused for its colours and page-size helper, never modified.
    """

    def __init__(self, *, footer: str, watermarks: Sequence[str], fonts: FontSet) -> None:
        self._footer = footer
        self._watermarks = tuple(watermarks)
        self._fonts = fonts

    def __call__(self, canvas: pdf_canvas.Canvas, _doc: BaseDocTemplate) -> None:
        width, height = pdf_parts.canvas_pagesize(canvas)
        canvas.saveState()
        canvas.setStrokeColor(pdf_parts.NAVY)
        canvas.setLineWidth(2)
        canvas.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)
        canvas.setFont(self._fonts.normal, 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(18 * mm, 10 * mm, self._footer)
        canvas.drawRightString(width - 18 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        self._draw_watermarks(canvas, width=width, height=height)
        canvas.restoreState()

    def _draw_watermarks(self, canvas: pdf_canvas.Canvas, *, width: float, height: float) -> None:
        if not self._watermarks:
            return
        # Lines are stacked about the centre on a fixed pitch, and each is then
        # sized to the room actually available at its own offset.
        pitch = _WATERMARK_MAX_SIZE * 1.4
        first = (len(self._watermarks) - 1) * pitch / 2
        offsets = [first - index * pitch for index in range(len(self._watermarks))]
        sizes = [
            _fit_font_size(
                line,
                self._fonts.bold,
                max_width=_diagonal_span(width, height, offset=offset) * _WATERMARK_FILL,
                max_size=_WATERMARK_MAX_SIZE,
            )
            for line, offset in zip(self._watermarks, offsets, strict=True)
        ]
        canvas.saveState()
        canvas.setFillColor(pdf_parts.WATERMARK_GREY)
        canvas.translate(width / 2, height / 2)
        canvas.rotate(45)
        for line, size, offset in zip(self._watermarks, sizes, offsets, strict=True):
            canvas.setFont(self._fonts.bold, size)
            canvas.drawCentredString(0, offset - size * 0.35, line)
        canvas.restoreState()


class IcaapPdfRenderer:
    """Builds the platypus story for one document, counting what it could not draw.

    Public because P3's filing PDF extends it: that renderer passes an embedded
    Unicode :class:`FontSet` (D-022) and a document with no watermark, then
    overrides :meth:`build_story` to splice its attestation page in at page 1
    and its annexes after the sections. Everything else — the escaping, the
    table builder, the checklist and the provenance page — is shared, so a
    filed ICAAP and a draft of the same cycle cannot show different text.
    """

    def __init__(self, document: IcaapDocument, *, fonts: FontSet) -> None:
        self._doc = document
        self._fonts = fonts
        self._styles = _styles(fonts)
        #: Characters this font could not draw, counted as the story is built.
        self.substituted = 0

    # -- text safety ------------------------------------------------------
    def _escape(self, text: str) -> str:
        """User text made safe for ``Paragraph``, drawn in THIS document's font.

        The substitution is the FONT SET's, not ``pdf_parts``' fixed
        Windows-1252 one. That distinction is the whole of D-022: with the
        default :data:`HELVETICA` the two are the same function and a draft's
        bytes are unchanged, but P3's filing PDF passes an embedded Unicode
        family, and routing its text through the Latin-1 test would have
        printed ``?`` for the cedi sign while the counter — which already asked
        the font set — reported that nothing had been substituted.
        """
        self.substituted += self._count_substitutions(text)
        normalized = self._fonts.printable(text).replace("\r\n", "\n").replace("\r", "\n")
        return "<br/>".join(xml_escape(line) for line in normalized.split("\n"))

    def _count_substitutions(self, text: str) -> int:
        normalized = xml_safe(text)
        drawn = self._fonts.printable(text)
        if len(normalized) != len(drawn):
            # ``printable`` is length-preserving today; if that ever changes,
            # under-reporting is safer than raising inside an export.
            return 0
        return sum(
            1
            for source, shown in zip(normalized, drawn, strict=True)
            if shown == pdf_parts.UNPRINTABLE_SUBSTITUTE
            and source != pdf_parts.UNPRINTABLE_SUBSTITUTE
        )

    def _para(self, text: str, style: str) -> Paragraph:
        return Paragraph(self._escape(text), self._styles[style])

    def _markup(self, text: str, *, bold: bool, italic: bool, underline: bool) -> str:
        escaped = self._escape(text)
        if bold:
            escaped = f"<b>{escaped}</b>"
        if italic:
            escaped = f"<i>{escaped}</i>"
        if underline:
            escaped = f"<u>{escaped}</u>"
        return escaped

    # -- editor IR --------------------------------------------------------
    def _run_markup(self, run: Run) -> str:
        if run.line_break:
            return "<br/>"
        fact = run.fact
        # A factRef prints the figure the block was bound to — the value the
        # author saw when they inserted it — not the editor's placeholder.
        text = self._doc.fact(fact.block_id, fact.fact_key) if fact is not None else run.text
        return self._markup(text, bold=run.bold, italic=run.italic, underline=run.underline)

    def _render_blocks(self, blocks: Iterable[RenderBlock]) -> list[Any]:
        story: list[Any] = []
        for block in blocks:
            story.extend(self._render_block(block))
        return story

    def _render_block(self, block: RenderBlock) -> list[Any]:
        if isinstance(block, ParagraphBlock):
            markup = "".join(self._run_markup(run) for run in block.runs)
            if not markup:
                return []
            style = block.style if block.style in self._styles else "body"
            return [Paragraph(markup, self._styles[style])]
        if isinstance(block, ListBlock):
            return [self._render_list(block)]
        if isinstance(block, QuoteBlock):
            return self._render_quote(block)
        if isinstance(block, DataBlockRef):
            return self._render_data_block(block.block_id)
        return []

    def _render_list(self, block: ListBlock) -> ListFlowable:
        # ``list[Any]``: reportlab's own stubs type ``ListFlowable(flowables=)``
        # as plain flowables, although ``ListItem`` is exactly what it expects.
        items: list[Any] = [
            ListItem(self._render_blocks(item) or [Spacer(0, 0)], leftIndent=12)
            for item in block.items
        ]
        if block.ordered:
            return ListFlowable(
                items,
                bulletType="1",
                # "1." rather than a bare "1", so the numbering reads the same
                # here as in the DOCX, which writes its markers out by hand.
                bulletFormat="%s.",
                start=max(1, int(block.start)),
                leftIndent=12,
            )
        return ListFlowable(items, bulletType="bullet", start="•", leftIndent=12)

    def _render_quote(self, block: QuoteBlock) -> list[Any]:
        story: list[Any] = []
        for child in block.children:
            for flowable in self._render_block(child):
                if isinstance(flowable, Paragraph):
                    story.append(Paragraph(flowable.text, self._styles["quote"]))
                else:
                    story.append(flowable)
        return story

    # -- data blocks ------------------------------------------------------
    def _render_data_block(self, block_id: str) -> list[Any]:
        block = self._doc.block(block_id)
        if block is None:
            # The section references a block the cycle no longer has. Say so:
            # dropping it would leave the sentence around it pointing at
            # nothing.
            return [
                self._para(
                    "A figure table referenced here is no longer part of this cycle.", "note"
                )
            ]
        story: list[Any] = [Spacer(0, 3 * mm), self._para(block.title, "h3")]
        meta = self._block_meta_line(block)
        if meta:
            story.append(self._para(meta, "note"))
        if block.status_note:
            story.append(self._para(block.status_note, "note"))
        if not block.tables:
            story.append(self._para(NOT_AVAILABLE, "body"))
        for table in block.tables:
            story.extend(self._table(table))
        for note in block.notes:
            story.append(self._para(note, "note"))
        story.append(Spacer(0, 3 * mm))
        return story

    def _block_meta_line(self, block: BlockRender) -> str:
        parts = [part for part in (block.source_label,) if part]
        if block.as_of is not None:
            parts.append(f"As at {fmt.format_date(block.as_of)}")
        parts.append(fmt.block_status_label(block.status))
        return " · ".join(parts)

    def _table(self, spec: TableSpec) -> list[Any]:
        if not spec.columns:
            return [self._para(f"{spec.title}: {NOT_AVAILABLE}", "body")]
        story: list[Any] = []
        if spec.title:
            story.append(self._para(spec.title, "h4"))
        header = [
            Paragraph(self._escape(column.label), self._styles["cell_head"])
            for column in spec.columns
        ]
        body: list[list[Any]] = [header]
        emphasis_rows: list[int] = []
        for index, row in enumerate(spec.rows, start=1):
            if row.emphasis:
                emphasis_rows.append(index)
            body.append(
                [
                    Paragraph(
                        self._escape(row.cells.get(column.key, "")),
                        self._styles["cell_right" if column.kind in fmt.NUMERIC_KINDS else "cell"],
                    )
                    for column in spec.columns
                ]
            )
        if not spec.rows:
            body.append(
                [
                    Paragraph(self._escape("No rows recorded."), self._styles["cell"]),
                    *[Paragraph("", self._styles["cell"]) for _ in spec.columns[1:]],
                ]
            )
        width = _PAGE[0] - _MARGINS["leftMargin"] - _MARGINS["rightMargin"]
        table = Table(body, colWidths=[width / len(spec.columns)] * len(spec.columns), repeatRows=1)
        style: list[tuple[Any, ...]] = [
            ("GRID", (0, 0), (-1, -1), 0.5, _GRID_GREY),
            ("BACKGROUND", (0, 0), (-1, 0), _HEADER_GREY),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        style.extend(("BACKGROUND", (0, row), (-1, row), _TOTAL_GREY) for row in emphasis_rows)
        table.setStyle(TableStyle(style))
        story.append(table)
        story.append(Spacer(0, 2 * mm))
        return story

    # -- document parts ---------------------------------------------------
    def _cover(self) -> list[Any]:
        doc = self._doc
        story: list[Any] = [
            Spacer(0, 24 * mm),
            self._para(doc.cycle_title, "title"),
            Spacer(0, 4 * mm),
        ]
        rows = [
            ("Institution", doc.institution_name),
            ("Financial year", f"FY{doc.fiscal_year}"),
            ("Reporting date", fmt.format_date(doc.as_of)),
            ("Basis", doc.basis_label),
            ("Cycle", doc.cycle_kind_label),
            ("Framework", f"{doc.framework_title} {doc.framework_version}"),
            ("Reporting currency", doc.currency),
            ("Generated at", doc.generated_at.isoformat(timespec="seconds")),
        ]
        table = Table(
            [
                [
                    Paragraph(self._escape(label), self._styles["cell_head"]),
                    Paragraph(self._escape(value), self._styles["cell"]),
                ]
                for label, value in rows
            ],
            colWidths=[45 * mm, 115 * mm],
        )
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, _GRID_GREY),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(0, 6 * mm))
        for line in self._doc.watermark_lines:
            story.append(self._para(line, "h3"))
        if doc.is_exposure_draft:
            story.append(self._para(EXPOSURE_DRAFT_NOTICE, "note"))
        if doc.framework_digest_note:
            story.append(self._para(doc.framework_digest_note, "note"))
        if doc.readiness_summary:
            story.append(Spacer(0, 4 * mm))
            story.append(self._para("Readiness", "h3"))
            for line in doc.readiness_summary:
                story.append(self._para(line, "small"))
        return story

    def _contents(self) -> list[Any]:
        story: list[Any] = [PageBreak(), self._para("Contents", "h1")]
        if not self._doc.sections:
            story.append(self._para("This cycle has no sections.", "body"))
            return story
        rows = [
            [
                Paragraph(self._escape(f"({section.letter})"), self._styles["cell"]),
                Paragraph(self._escape(section.title), self._styles["cell"]),
                Paragraph(self._escape(section.content_label), self._styles["cell"]),
            ]
            for section in self._doc.sections
        ]
        table = Table(rows, colWidths=[14 * mm, 96 * mm, 50 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.25, _GRID_GREY),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story.append(table)
        return story

    def _section(self, section: SectionRender) -> list[Any]:
        story: list[Any] = [
            PageBreak(),
            self._para(f"({section.letter}) {section.title}", "h1"),
        ]
        meta = " · ".join(part for part in (section.citation_label, section.content_label) if part)
        if meta:
            story.append(self._para(meta, "note"))
        if section.source_status and section.source_status != "sourced":
            story.append(
                self._para(fmt.pending_primary_text_note(self._doc.regulator_short), "note")
            )
        story.append(Spacer(0, 2 * mm))
        prose = self._render_blocks(section.blocks)
        if prose:
            story.extend(prose)
        else:
            story.append(self._para(section.empty_note or "", "note"))
        return story

    def _checklist(self) -> list[Any]:
        lines = [(section, line) for section in self._doc.sections for line in section.checklist]
        story: list[Any] = [PageBreak(), self._para("Requirement checklist", "h1")]
        if not lines:
            story.append(self._para("No requirements are recorded for this framework.", "body"))
            return story
        header = ["Section", "Requirement", "Citation", "Status", "Reason"]
        body: list[list[Any]] = [
            [Paragraph(self._escape(label), self._styles["cell_head"]) for label in header]
        ]
        for section, line in lines:
            body.append(
                [
                    Paragraph(self._escape(f"({section.letter})"), self._styles["cell"]),
                    Paragraph(self._escape(line.text), self._styles["cell"]),
                    Paragraph(self._escape(line.citation_label), self._styles["cell"]),
                    Paragraph(
                        self._escape(fmt.requirement_status_label(line.status)),
                        self._styles["cell"],
                    ),
                    # REG-ICAAP-008: the reason a requirement was set aside has
                    # to reach the document, not only the screen.
                    Paragraph(self._escape(line.reason or ""), self._styles["cell"]),
                ]
            )
        table = Table(body, colWidths=[17 * mm, 58 * mm, 26 * mm, 26 * mm, 47 * mm], repeatRows=1)
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
        return story

    def _attachments(self) -> list[Any]:
        story: list[Any] = [PageBreak(), self._para("Attachments", "h1")]
        if not self._doc.attachments:
            story.append(self._para("No documents have been attached to this cycle.", "body"))
            return story
        header = ["Kind", "Title", "Checksum", "Uploaded", "Status"]
        body: list[list[Any]] = [
            [Paragraph(self._escape(label), self._styles["cell_head"]) for label in header]
        ]
        for attachment in self._doc.attachments:
            body.append(
                [
                    Paragraph(self._escape(attachment.kind_title), self._styles["cell"]),
                    Paragraph(self._escape(attachment.title), self._styles["cell"]),
                    Paragraph(self._escape(attachment.sha256_prefix), self._styles["cell"]),
                    Paragraph(
                        self._escape(fmt.format_date(attachment.uploaded_on)),
                        self._styles["cell"],
                    ),
                    Paragraph(
                        self._escape("Withdrawn" if attachment.withdrawn else "Active"),
                        self._styles["cell"],
                    ),
                ]
            )
        table = Table(body, colWidths=[34 * mm, 62 * mm, 32 * mm, 24 * mm, 22 * mm], repeatRows=1)
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
        return story

    def _provenance(self) -> list[Any]:
        doc = self._doc
        story: list[Any] = [PageBreak(), self._para("Provenance", "h1")]
        lines = [
            f"Framework: {doc.framework_title} {doc.framework_version} ({doc.framework_code})",
            f"Framework status: {fmt.humanize(doc.framework_status)}",
            f"Framework digest: {doc.framework_digest}",
            f"Generated at: {doc.generated_at.isoformat(timespec='seconds')}",
            DRAFT_PROVENANCE_NOTICE,
        ]
        if doc.framework_digest_note:
            lines.insert(3, doc.framework_digest_note)
        if doc.is_exposure_draft:
            lines.insert(2, EXPOSURE_DRAFT_NOTICE)
        for line in lines:
            story.append(self._para(line, "small"))
        story.append(Spacer(0, 3 * mm))
        story.append(self._para("Figure sources", "h3"))
        sourced = [block for block in doc.blocks.values() if block.provenance or block.source_label]
        if not sourced:
            story.append(self._para("No figure tables are bound in this cycle.", "small"))
        for block in sourced:
            story.append(self._para(block.title, "cell_head"))
            detail = [f"Status: {fmt.block_status_label(block.status)}"]
            if block.source_label:
                detail.append(block.source_label)
            if block.as_of is not None:
                detail.append(f"As at {fmt.format_date(block.as_of)}")
            detail.extend(f"{label}: {value}" for label, value in block.provenance)
            story.append(self._para(" · ".join(detail), "small"))
        story.append(Spacer(0, 3 * mm))
        story.append(KeepTogether(self._glyph_note()))
        return story

    def _glyph_note(self) -> list[Any]:
        """DV-004, stated plainly and without an exactness claim."""
        story: list[Any] = [self._para("Character coverage", "h3")]
        if self.substituted:
            story.append(
                self._para(
                    f"{self.substituted} character(s) in this export are outside the range of "
                    f"the font used for this draft and are shown as "
                    f"'{pdf_parts.UNPRINTABLE_SUBSTITUTE}'. The exact text is held in the "
                    f"workspace record and in each section's stored version.",
                    "small",
                )
            )
        else:
            story.append(
                self._para(
                    "Characters outside the range of the font used for this draft are shown "
                    f"as '{pdf_parts.UNPRINTABLE_SUBSTITUTE}'; the exact text is held in the "
                    "workspace record. No character in this export needed substituting.",
                    "small",
                )
            )
        return story

    # -- assembly ---------------------------------------------------------
    def _footer(self) -> str:
        doc = self._doc
        return self._fonts.printable(
            f"{doc.institution_name} · ICAAP FY{doc.fiscal_year} ({doc.basis_label}) · "
            f"generated {doc.generated_at.isoformat(timespec='seconds')} · "
            f"framework {doc.framework_code} {doc.framework_version}"
        )

    def build_story(self) -> list[Any]:
        """Every flowable, in printing order. Override to insert or reorder parts."""
        story: list[Any] = [
            *self._cover(),
            *self._contents(),
        ]
        for section in self._doc.sections:
            story.extend(self._section(section))
        story.extend(self._checklist())
        story.extend(self._attachments())
        # The provenance page is built last so the substitution count covers
        # every other page.
        story.extend(self._provenance())
        return story

    def build(self) -> bytes:
        story = self.build_story()
        buffer = io.BytesIO()
        template = BaseDocTemplate(
            buffer,
            pagesize=_PAGE,
            **_MARGINS,
            title=self._doc.cycle_title,
            author="AequorOS",
            subject=f"ICAAP FY{self._doc.fiscal_year} ({self._doc.basis_label})",
        )
        width, height = _PAGE
        template.addPageTemplates(
            [
                PageTemplate(
                    id=_TEMPLATE_ID,
                    pagesize=_PAGE,
                    frames=[
                        Frame(
                            _MARGINS["leftMargin"],
                            _MARGINS["bottomMargin"],
                            width - _MARGINS["leftMargin"] - _MARGINS["rightMargin"],
                            height - _MARGINS["topMargin"] - _MARGINS["bottomMargin"],
                            id="content",
                        )
                    ],
                    onPage=DraftFurniture(
                        footer=self._footer(),
                        watermarks=self._doc.watermark_lines,
                        fonts=self._fonts,
                    ),
                )
            ]
        )
        template.build(story, canvasmaker=pdf_parts.invariant_canvas)
        return buffer.getvalue()


def render_pdf_with_report(
    document: IcaapDocument, *, fonts: FontSet = HELVETICA
) -> tuple[bytes, int]:
    """The PDF bytes and how many characters the font could not draw.

    The count is the number the provenance page states. It is returned as well
    so a caller — a test, or P3 deciding whether its embedded font is doing its
    job — can assert on it without parsing the document back.
    """
    renderer = IcaapPdfRenderer(document, fonts=fonts)
    payload = renderer.build()
    return payload, renderer.substituted


def render_pdf(document: IcaapDocument, *, fonts: FontSet = HELVETICA) -> bytes:
    """One ICAAP document as PDF bytes. Same input, same bytes."""
    return render_pdf_with_report(document, fonts=fonts)[0]


__all__ = [
    "HELVETICA",
    "DraftFurniture",
    "FontSet",
    "IcaapPdfRenderer",
    "render_pdf",
    "render_pdf_with_report",
]
