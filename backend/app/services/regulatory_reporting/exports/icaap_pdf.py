"""The ICAAP filing PDF — the document an officer signs and a regulator reads.

This is the filed instrument. ``filing_format="pdf"`` on the registry entry says
so, and there is no workbook alongside it: the regulator publishes no ICAAP
template, so an auto-exported spreadsheet would be a reconstruction nobody asked
for (the Word file this module's sibling writes is a working copy and is
excluded from the filing set).

It differs from P1's draft PDF in four ways and shares everything else, which is
deliberate — a filed ICAAP and a draft of the same cycle must not be able to
show different text:

1. **It is set in an embedded Unicode family** (``icaap_fonts``, D-022), so the
   cedi sign and Ghanaian vowels are drawn rather than substituted. That is the
   real fix deviation DV-004 deferred to exactly here.
2. **It carries an attestation page at page index 1**, whose ruled signing
   blocks are drawn at the coordinates ``pdf_signing`` will place AcroForm
   fields on. The two are not two copies of one layout: this page draws the
   rules from ``pdf_signing.default_placements(order, layout="icaap")``, so the
   only way they can disagree is if somebody deletes that call.
3. **It renders from the frozen snapshot**, never from the workspace.
4. **It carries no DRAFT watermark** unless the caller asks for one, and its
   provenance page states the package, its digests, its source runs, the
   governed parameters it relied on and how many paragraphs were AI-assisted.

**Two page shapes, both correct.** The Board signature slot ships disabled
(D-043), so under the default policy an ICAAP report has preparer and approver
blocks only — and, in place of a third block, the line that says where Board
approval actually lives: the Board resolution filed with the report. A bank that
turns the slot on in Settings gets three blocks. Which shape is drawn comes from
the signing order the ceremony will use, never from a constant here.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from typing import Any, Final

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.domain.icaap.document import EXPOSURE_DRAFT_NOTICE
from app.services.attestation import pdf_signing
from app.services.icaap.render import format as fmt
from app.services.icaap.render.from_snapshot import (
    FilingProvenance,
    IcaapFiling,
    SnapshotTable,
)

# The page size and margins come from P1's renderer rather than being restated:
# the filing PDF and the draft of the same cycle are the same document in the
# same measure, and two copies of a margin is how two documents of one cycle
# come to paginate differently.
from app.services.icaap.render.pdf import (  # noqa: E402 - grouped with its explanation
    _MARGINS,
    _PAGE,
    DraftFurniture,
    FontSet,
    IcaapPdfRenderer,
)
from app.services.regulatory_reporting.exports import icaap_fonts, pdf_parts

#: The artifact layout whose attestation page this module draws.
LAYOUT: Final = "icaap"

_COVER_TEMPLATE: Final = "icaap-filing-cover"
_ATTESTATION_TEMPLATE: Final = "icaap-filing-attestation"
_CONTENT_TEMPLATE: Final = "icaap-filing-content"

#: What each ruled cell is labelled, under its rule. Same four things the
#: standard return's attestation block asks for (``exports/pdf.py``), because
#: they are what a BoG attestation block asks each officer for.
CELL_LABELS: Final[Mapping[str, str]] = {
    "name": "Name",
    "title": "Designation",
    pdf_signing.SIGNATURE_FIELD_TYPE: "Signature",
    "date_signed": "Date",
    "initials": "Initials",
}

#: Printed in place of the third block when the Board slot is disabled (D-043).
#: The absence of a Board signature must not read as an absence of Board
#: approval: ¶45 and ¶71 put that approval in the resolution filed alongside,
#: and submission is refused without one.
BOARD_RESOLUTION_NOTICE: Final = (
    "Board approval is evidenced by the Board resolution filed with this report."
)

#: Clear space between a role's signature box and the line printed above it.
_ROLE_LINE_GAP: Final = 8
#: Clear space between a rule and the small label printed under it.
_CELL_LABEL_DROP: Final = 9
_CELL_LABEL_SIZE: Final = 6.5
_ROLE_LINE_SIZE: Final = 9
#: The attestation page's text frame stops here, clear of the topmost block.
_ATTESTATION_FRAME_CLEARANCE: Final = 24


# --------------------------------------------------------------------------
# The attestation page's furniture
# --------------------------------------------------------------------------
class AttestationFurniture(DraftFurniture):
    """The page furniture, plus one ruled signing block per officer.

    The rules are drawn on the canvas at absolute coordinates rather than laid
    out as a flowable table, because these particular lines are not decoration:
    ``pdf_signing`` creates an AcroForm field whose ``/Rect`` sits exactly on
    each of them, and a flowed table's position is a function of whatever text
    happened to precede it. The coordinates come from ``default_placements`` —
    the same call the field preparation makes — so the page and the fields have
    one source of geometry between them, not two.
    """

    def __init__(
        self,
        *,
        footer: str,
        watermarks: Sequence[str],
        fonts: FontSet,
        placements: Sequence[pdf_signing.FieldPlacement],
        role_lines: Mapping[str, str],
    ) -> None:
        super().__init__(footer=footer, watermarks=watermarks, fonts=fonts)
        self._placements = tuple(placements)
        self._role_lines = dict(role_lines)

    def __call__(self, canvas: pdf_canvas.Canvas, doc: BaseDocTemplate) -> None:
        super().__call__(canvas, doc)
        self._draw_blocks(canvas)

    def _draw_blocks(self, canvas: pdf_canvas.Canvas) -> None:
        canvas.saveState()
        for placement in self._placements:
            left, bottom, right, top = placement.box
            canvas.setStrokeColor(colors.black)
            canvas.setLineWidth(0.75)
            canvas.line(left, bottom, right, bottom)
            label = CELL_LABELS.get(placement.field_type)
            if label:
                canvas.setFont(self._fonts.normal, _CELL_LABEL_SIZE)
                canvas.setFillColor(colors.grey)
                canvas.drawString(left, bottom - _CELL_LABEL_DROP, label)
            if placement.field_type == pdf_signing.SIGNATURE_FIELD_TYPE:
                line = self._role_lines.get(placement.signing_role)
                if line:
                    canvas.setFont(self._fonts.bold, _ROLE_LINE_SIZE)
                    canvas.setFillColor(colors.black)
                    canvas.drawString(
                        _MARGINS["leftMargin"], top + _ROLE_LINE_GAP, line
                    )
        canvas.restoreState()


# --------------------------------------------------------------------------
# The renderer
# --------------------------------------------------------------------------
class IcaapFilingRenderer(IcaapPdfRenderer):
    """P1's renderer with the filing's own page order and provenance.

    Everything shared — escaping, the editor IR walker, the table builder, the
    checklist — is inherited unchanged, which is what keeps a filed ICAAP and a
    draft of the same cycle line-for-line comparable.
    """

    def __init__(self, filing: IcaapFiling, *, fonts: FontSet) -> None:
        super().__init__(filing.document, fonts=fonts)
        self._provenance_data: FilingProvenance = filing.provenance
        self._placements: tuple[pdf_signing.FieldPlacement, ...] = (
            pdf_signing.default_placements(filing.signing_order, layout=LAYOUT)
            if filing.signing_order
            else ()
        )
        self._role_lines: dict[str, str] = {}

    # -- attestation page -------------------------------------------------
    def _role_line(self, role: str) -> str:
        """The line printed above one officer's block.

        The framework's own frozen wording where the snapshot has it; otherwise
        the platform's role label. Never a regulator's name written here.
        """
        for slot in self._provenance_data.signature_slots:
            if slot.role == role and slot.line:
                return slot.line.rstrip()
        return pdf_signing.ROLE_LABELS.get(role, "Signed by")

    def _attestation_page(self) -> list[Any]:
        story: list[Any] = [self._para("Attestation", "h1")]
        doc = self._doc
        story.append(
            self._para(
                f"{doc.institution_name} · ICAAP FY{doc.fiscal_year} "
                f"({doc.basis_label}) · as at {self._provenance_data.reporting_date}",
                "note",
            )
        )
        story.append(Spacer(0, 4 * mm))
        for slot in self._provenance_data.signature_slots:
            if not slot.statement:
                continue
            story.append(self._para(self._role_line(slot.role), "h3"))
            story.append(self._para(slot.statement, "body"))
            story.append(Spacer(0, 2 * mm))
        if not self._has_board_block():
            story.append(Spacer(0, 2 * mm))
            story.append(self._para(BOARD_RESOLUTION_NOTICE, "body"))
        if not self._placements:
            story.append(Spacer(0, 4 * mm))
            story.append(
                self._para(
                    "This copy carries no signing blocks. The filed document is the "
                    "signed version of this report.",
                    "note",
                )
            )
        # The canvas callback needs these; the story is always built first.
        # ``_escape`` both counts what the font cannot draw — so the provenance
        # page's substitution figure covers the attestation page too — and
        # returns markup, which a canvas ``drawString`` must not be handed, so
        # the drawn string is the font's own printable form.
        self._role_lines = {}
        for placement in self._placements:
            line = self._role_line(placement.signing_role)
            self._escape(line)
            self._role_lines[placement.signing_role] = self._fonts.printable(line)
        return story

    def _has_board_block(self) -> bool:
        return any(placement.signing_role == "board" for placement in self._placements)

    # -- generic envelope tables -----------------------------------------
    def _snapshot_table(self, table: SnapshotTable, *, heading: str = "h2") -> list[Any]:
        story: list[Any] = [self._para(table.title, heading)]
        story.extend(self._table(table.spec))
        return story

    def _headline(self) -> list[Any]:
        headline = self._provenance_data.headline
        if headline is None:
            return []
        return [PageBreak(), *self._snapshot_table(headline, heading="h1")]

    def _annexes(self) -> list[Any]:
        tables = self._provenance_data.annex_tables
        entries = self._provenance_data.annexes
        if not tables and not entries:
            return []
        story: list[Any] = [PageBreak(), self._para("Annex A", "h1")]
        for entry in entries:
            name = (
                entry.return_code
                if entry.title == entry.return_code
                else f"{entry.title} ({entry.return_code})"
            )
            story.append(
                self._para(
                    f"{name} · package {entry.package_id} version {entry.version} "
                    f"· content digest {entry.content_digest}",
                    "note",
                )
            )
        if not tables:
            story.append(
                self._para(
                    "The annex return's own tables are not part of this snapshot.", "note"
                )
            )
        for table in tables:
            story.extend(self._snapshot_table(table))
        return story

    # -- attachments ------------------------------------------------------
    def _attachments(self) -> list[Any]:
        """The freeze-time manifest, printed with the columns it actually has.

        No "uploaded on" column: the manifest records what accompanied the
        filing and its checksums, not when each document was uploaded, and a
        date beside a checksum that was not the document's own date would be a
        fabricated fact on a filed page.
        """
        manifest = self._provenance_data.attachments
        story: list[Any] = [PageBreak(), self._para("Documents filed with this report", "h1")]
        if not manifest:
            story.append(
                self._para("No documents accompany this report.", "body")
            )
            return story
        header = ("Document", "Title", "Required at", "Size", "SHA-256")
        body: list[list[Any]] = [
            [self._cell(label, head=True) for label in header],
            *[
                [
                    self._cell(entry.kind_title),
                    self._cell(entry.title),
                    self._cell(_gate_label(entry.gate)),
                    self._cell(_byte_label(entry.byte_size)),
                    self._cell(entry.sha256),
                ]
                for entry in manifest
            ],
        ]
        story.append(self._grid(body, [34 * mm, 44 * mm, 22 * mm, 18 * mm, 56 * mm]))
        return story

    # -- provenance -------------------------------------------------------
    def _provenance(self) -> list[Any]:
        data = self._provenance_data
        doc = self._doc
        story: list[Any] = [PageBreak(), self._para("Provenance", "h1")]
        if doc.is_exposure_draft:
            story.append(
                self._para(data.exposure_draft_line or EXPOSURE_DRAFT_NOTICE, "h3")
            )
        story.append(self._para("This filing", "h2"))
        for label, value in (
            ("Return", data.return_code),
            ("Package", f"{data.package_id} (version {data.package_version})"),
            ("Reporting date", data.reporting_date),
            ("Content digest", data.content_digest),
            ("Snapshot seal", data.snapshot_sha256),
            ("Review digest", data.review_digest),
            ("Institution register digest", data.institution_register_digest),
            ("Generated at", doc.generated_at.isoformat(timespec="seconds")),
        ):
            story.append(self._para(f"{label}: {value}", "small"))

        story.append(Spacer(0, 3 * mm))
        story.append(self._para("Framework", "h2"))
        for line in (
            f"{doc.framework_title} {doc.framework_version} ({doc.framework_code})",
            f"Status: {doc.framework_status or 'Not recorded'}",
            f"Digest: {doc.framework_digest}",
        ):
            story.append(self._para(line, "small"))
        if doc.framework_digest_note:
            story.append(self._para(doc.framework_digest_note, "small"))

        story.append(Spacer(0, 3 * mm))
        story.extend(self._ai_assistance())
        story.append(Spacer(0, 3 * mm))
        story.extend(self._governed_parameters())
        story.append(Spacer(0, 3 * mm))
        story.extend(self._source_runs())
        story.append(Spacer(0, 3 * mm))
        story.extend(self._review_record())
        story.append(Spacer(0, 3 * mm))
        story.extend(self._figure_sources())
        story.append(Spacer(0, 3 * mm))
        story.extend(self._glyph_note())
        return story

    def _ai_assistance(self) -> list[Any]:
        count = self._provenance_data.ai_assisted_paragraphs
        story: list[Any] = [self._para("Drafting assistance", "h2")]
        if count:
            story.append(
                self._para(
                    f"{count} paragraph(s) in this report were drafted with AI assistance "
                    "and were reviewed and committed by a named officer before the report "
                    "was frozen.",
                    "small",
                )
            )
        else:
            story.append(
                self._para("No paragraph in this report was drafted with AI assistance.", "small")
            )
        return story

    def _governed_parameters(self) -> list[Any]:
        story: list[Any] = [self._para("Regulatory values applied", "h2")]
        parameters = self._provenance_data.parameters
        if not parameters:
            story.append(
                self._para("This report applies no governed regulatory value.", "small")
            )
            return story
        header = ("Parameter", "Value", "Unit", "Status", "Citation")
        body: list[list[Any]] = [
            [self._cell(label, head=True) for label in header],
            *[
                [
                    self._cell(entry.code),
                    self._cell(entry.value),
                    self._cell(entry.unit),
                    # A value the control plane has not confirmed says so on the
                    # filed page; it is not silently presented as settled.
                    self._cell(_confirmation_label(entry.confirmation_status)),
                    self._cell(entry.citation),
                ]
                for entry in parameters
            ],
        ]
        story.append(self._grid(body, [40 * mm, 20 * mm, 16 * mm, 30 * mm, 68 * mm]))
        return story

    def _source_runs(self) -> list[Any]:
        story: list[Any] = [self._para("Calculation runs", "h2")]
        runs = self._provenance_data.source_runs
        if not runs:
            story.append(
                self._para("No calculation run is bound to this report.", "small")
            )
            return story
        header = ("Module", "Run", "Input hash", "Engine")
        body: list[list[Any]] = [
            [self._cell(label, head=True) for label in header],
            *[
                [
                    self._cell(run.module),
                    self._cell(run.run_id),
                    self._cell(run.input_hash),
                    self._cell(run.engine_version),
                ]
                for run in runs
            ],
        ]
        story.append(self._grid(body, [30 * mm, 52 * mm, 62 * mm, 30 * mm]))
        return story

    def _review_record(self) -> list[Any]:
        story: list[Any] = [self._para("Review and approval", "h2")]
        stages = self._provenance_data.stages
        if not stages:
            story.append(self._para("No review decision is recorded in this snapshot.", "small"))
            return story
        header = ("Stage", "Decision", "By", "Designation", "When")
        body: list[list[Any]] = [
            [self._cell(label, head=True) for label in header],
            *[
                [
                    self._cell(stage.title),
                    self._cell(stage.decision),
                    self._cell(stage.decided_by_name),
                    self._cell(stage.officer_title),
                    self._cell(stage.decided_at),
                ]
                for stage in stages
            ],
        ]
        story.append(self._grid(body, [34 * mm, 26 * mm, 40 * mm, 34 * mm, 40 * mm]))
        return story

    def _figure_sources(self) -> list[Any]:
        story: list[Any] = [self._para("Figure sources", "h2")]
        sourced = [
            block
            for block in self._doc.blocks.values()
            if block.provenance or block.source_label
        ]
        if not sourced:
            story.append(self._para("No figure table is bound in this report.", "small"))
            return story
        for block in sourced:
            story.append(self._para(block.title, "cell_head"))
            detail = [f"Status: {fmt.block_status_label(block.status)}"]
            if block.source_label:
                detail.append(block.source_label)
            if block.as_of is not None:
                detail.append(f"As at {fmt.format_date(block.as_of)}")
            detail.extend(f"{label}: {value}" for label, value in block.provenance)
            story.append(self._para(" · ".join(detail), "small"))
        return story

    def _glyph_note(self) -> list[Any]:
        """DV-004, told truthfully for the font that actually set this page."""
        story: list[Any] = [self._para("Character coverage", "h2")]
        if self.substituted:
            story.append(
                self._para(
                    f"{self.substituted} character(s) in this report are outside the "
                    "repertoire of the embedded font and are shown as a substitute. The "
                    "exact text is held in the sealed snapshot this document was rendered "
                    "from.",
                    "small",
                )
            )
        else:
            story.append(
                self._para(
                    "This report is set in an embedded Unicode font. Every character in "
                    "it, including the reporting currency's symbol, is drawn as written.",
                    "small",
                )
            )
        return story

    # -- small helpers -----------------------------------------------------
    def _cell(self, text: str, *, head: bool = False) -> Any:
        return Paragraph(self._escape(text), self._styles["cell_head" if head else "cell"])

    def _grid(self, body: list[list[Any]], widths: list[float]) -> Table:
        table = Table(body, colWidths=widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFBFBF")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9D9D9")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return table

    # -- assembly ---------------------------------------------------------
    def build_story(self) -> list[Any]:
        story: list[Any] = [
            *self._cover(),
            NextPageTemplate(_ATTESTATION_TEMPLATE),
            PageBreak(),
            # Switched back BEFORE the attestation content, not after it. A
            # ``NextPageTemplate`` takes effect at the next page break, and an
            # attestation page whose statements overflow breaks inside this
            # content — so leaving the switch until afterwards drew a SECOND
            # (and third) set of empty signing rules on the overflow pages,
            # only one of which ever carries a signature field. Page 1 is
            # already under the attestation template; every page after it is
            # content, whatever the length of the frozen statements.
            NextPageTemplate(_CONTENT_TEMPLATE),
            *self._attestation_page(),
            *self._contents(),
        ]
        story.extend(self._headline())
        for section in self._doc.sections:
            story.extend(self._section(section))
        story.extend(self._checklist())
        story.extend(self._annexes())
        story.extend(self._attachments())
        # Last, so the substitution count covers every other page.
        story.extend(self._provenance())
        return story

    def build(self) -> bytes:
        story = self.build_story()
        buffer = io.BytesIO()
        template = BaseDocTemplate(
            buffer,
            pagesize=_PAGE,
            **_MARGINS,
            title=f"{self._provenance_data.return_code} — {self._doc.cycle_title}",
            author="AequorOS",
            subject=f"ICAAP FY{self._doc.fiscal_year} ({self._doc.basis_label})",
        )
        template.addPageTemplates(self._page_templates())
        template.build(story, canvasmaker=pdf_parts.invariant_canvas)
        return buffer.getvalue()

    def _page_templates(self) -> list[PageTemplate]:
        footer = self._footer()
        watermarks = self._doc.watermark_lines
        plain = DraftFurniture(footer=footer, watermarks=watermarks, fonts=self._fonts)
        attestation = AttestationFurniture(
            footer=footer,
            watermarks=watermarks,
            fonts=self._fonts,
            placements=self._placements,
            role_lines=self._role_lines,
        )
        return [
            PageTemplate(
                id=_COVER_TEMPLATE, pagesize=_PAGE, frames=[_full_frame()], onPage=plain
            ),
            PageTemplate(
                id=_ATTESTATION_TEMPLATE,
                pagesize=_PAGE,
                frames=[self._attestation_frame()],
                onPage=attestation,
            ),
            PageTemplate(
                id=_CONTENT_TEMPLATE, pagesize=_PAGE, frames=[_full_frame()], onPage=plain
            ),
        ]

    def _attestation_frame(self) -> Frame:
        """The text region of the attestation page: above every signing block.

        The floor is computed from the blocks themselves — the top of the
        highest box plus the line printed above it plus clearance — so adding a
        third block moves the text up rather than letting a statement run
        through a signature.
        """
        width, height = _PAGE
        top = height - _MARGINS["topMargin"]
        bottom = _MARGINS["bottomMargin"]
        if self._placements:
            highest = max(placement.box[3] for placement in self._placements)
            bottom = highest + _ROLE_LINE_GAP + _ROLE_LINE_SIZE + _ATTESTATION_FRAME_CLEARANCE
        return Frame(
            _MARGINS["leftMargin"],
            bottom,
            width - _MARGINS["leftMargin"] - _MARGINS["rightMargin"],
            max(0.0, top - bottom),
            id="attestation",
        )


def _full_frame() -> Frame:
    width, height = _PAGE
    return Frame(
        _MARGINS["leftMargin"],
        _MARGINS["bottomMargin"],
        width - _MARGINS["leftMargin"] - _MARGINS["rightMargin"],
        height - _MARGINS["topMargin"] - _MARGINS["bottomMargin"],
        id="content",
    )


def _gate_label(gate: str) -> str:
    return {
        "freeze": "Before freeze",
        "submission": "Before submission",
        "optional": "Optional",
    }.get(gate, gate or "")


def _byte_label(byte_size: int | None) -> str:
    if byte_size is None:
        return ""
    if byte_size < 1024:
        return f"{byte_size} B"
    if byte_size < 1024 * 1024:
        return f"{byte_size / 1024:.0f} KB"
    return f"{byte_size / (1024 * 1024):.1f} MB"


def _confirmation_label(status: str) -> str:
    if status == "pending":
        return "Pending confirmation"
    if status == "confirmed":
        return "Confirmed"
    return status or ""


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------
def render_filing_pdf_with_report(filing: IcaapFiling) -> tuple[bytes, int]:
    """The filing PDF and how many characters the embedded font could not draw.

    The count is what the provenance page states, returned as well so a caller —
    or a test asking whether the embedded font is doing its job — can assert on
    it without parsing the document back.
    """
    renderer = IcaapFilingRenderer(filing, fonts=icaap_fonts.register_noto())
    payload = renderer.build()
    return payload, renderer.substituted


def render_filing_pdf(filing: IcaapFiling) -> bytes:
    """One frozen ICAAP package as the filing PDF. Same input, same bytes."""
    return render_filing_pdf_with_report(filing)[0]


__all__ = [
    "BOARD_RESOLUTION_NOTICE",
    "CELL_LABELS",
    "LAYOUT",
    "AttestationFurniture",
    "IcaapFilingRenderer",
    "render_filing_pdf",
    "render_filing_pdf_with_report",
]
