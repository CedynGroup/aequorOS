"""The render model an ICAAP document is printed from (pure; no I/O, no renderer types).

One model, two renderers, and later a third reader. ``render/pdf.py`` and
``render/docx.py`` (P1 drafts) consume nothing but an :class:`IcaapDocument`,
and P3's filing PDF and working-copy DOCX build the same object from the frozen
package snapshot (``render/from_snapshot.py``). Keeping the model here — in the
pure domain, with no reportlab or python-docx import — is what makes that
possible: the renderers never reach back into the database, so a filed document
and a draft of the same cycle differ only in the fields set on this object.

**Nothing in here computes or invents a value.** Every number, date and label
reaches the page as a string that a service resolved from a block binding, a
framework definition or the bank's own record (founder directive D-024: no
regulatory number lives in code). A fact with no value renders
:data:`NOT_AVAILABLE` — never ``0``, which would read as a measured zero.

**Section prose is the editor's own IR.** It arrives as the render blocks
``app/domain/icaap/prosemirror.to_render_blocks`` produces from the stored
ProseMirror JSON, re-exported here so every renderer has one import site: the
prose grammar can move without touching the writers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.icaap.prosemirror import (
    DataBlockRef,
    FactRef,
    ListBlock,
    ParagraphBlock,
    ParagraphStyle,
    QuoteBlock,
    RenderBlock,
    Run,
)

#: Drafts carry this on every page. It is not a disclaimer to be tuned: an
#: un-frozen ICAAP has not been through the review chain, and a page of it that
#: reached a supervisor without saying so would be a misrepresentation.
DRAFT_WATERMARK = "DRAFT — not approved"

#: D-029. A rehearsal cycle walks the entire lifecycle, so the watermark is the
#: only thing on the page that distinguishes its output from a real filing.
REHEARSAL_WATERMARK = "REHEARSAL — not a regulatory filing"

#: The DOCX is an editable copy for the bank's own reviewers. The filed
#: instrument is always the signed PDF.
WORKING_COPY_NOTICE = "WORKING COPY — not the filed document"

#: What an absent fact prints as. Never "0", never blank.
NOT_AVAILABLE = "Not available"

#: The framework status that means the regulator has not finalised the text.
EXPOSURE_DRAFT_STATUS = "exposure_draft"
EXPOSURE_DRAFT_NOTICE = "Exposure draft — the regulator may change this text"

#: Printed on the provenance page of every draft.
DRAFT_PROVENANCE_NOTICE = "Draft export: not a regulatory filing."

#: Section content labels (the service picks one; they are production copy).
COMMITTED_CONTENT_LABEL = "Version {version} (committed)"
WORKING_CONTENT_LABEL = "Working draft — not committed"
#: The content label when the committed view is asked for and there is none.
#: It must not read "Working draft", which would claim the draft is printed.
NO_COMMITTED_VERSION_LABEL = "No committed version"
NO_COMMITTED_TEXT = "No committed text yet."
NO_TEXT_YET = "No text has been written for this section yet."


# --------------------------------------------------------------------------
# Editor render IR (defined by ``prosemirror.py``; re-exported, not redefined)
# --------------------------------------------------------------------------
# ``RenderBlock`` and its members are re-exported (see ``__all__``) so the
# renderers import the whole render model from one place: a draft and the filed
# document render from exactly the same structure, which is what makes them
# comparable line by line.

#: The paragraph styles the editor allows. ``h1`` is reserved for the section
#: title, which the renderer draws itself.
PARAGRAPH_STYLES = ("body", "h2", "h3", "h4")


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TableColumn:
    """One column of a data block's table.

    ``kind`` is the payload's declared cell kind (``text``, ``amount``,
    ``ratio_pct``, ``count`` …). The renderers use it only to decide alignment:
    the cell text is already formatted by ``render/format.py``, because the
    same string has to appear in the PDF and the DOCX.
    """

    key: str
    label: str
    kind: str


@dataclass(frozen=True)
class TableRow:
    """One row. ``cells`` holds display strings keyed by column key; a key the
    row omits prints blank. ``emphasis`` (e.g. ``"total"``) is a presentation
    hint the payload set, never a computed subtotal."""

    cells: Mapping[str, str]
    emphasis: str | None = None


@dataclass(frozen=True)
class TableSpec:
    key: str
    title: str
    columns: tuple[TableColumn, ...]
    rows: tuple[TableRow, ...]


@dataclass(frozen=True)
class BlockRender:
    """A bound data block as it prints: its tables, where the figures came
    from, and — when the binding is no longer current — what is wrong with it.

    ``status_note`` is the honest line ("Newer figures available", "Pinned …").
    It is always printed beside the table: a stale table with no note is a
    figure presented as current when it is not.
    """

    block_id: str
    title: str
    as_of: date | None
    source_label: str
    status: str
    status_note: str | None = None
    tables: tuple[TableSpec, ...] = ()
    notes: tuple[str, ...] = ()
    #: ``(label, value)`` pairs for the provenance page — run id, input hash,
    #: engine version, package/sign-off ids as the resolver recorded them.
    provenance: tuple[tuple[str, str], ...] = ()


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ChecklistLine:
    """One framework requirement and what the bank said about it.

    ``reason`` reaches the artifact deliberately: a requirement marked not
    applicable without its reason is an unevidenced claim (REG-ICAAP-008).
    """

    item_id: str
    text: str
    citation_label: str
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class SectionRender:
    key: str
    letter: str
    title: str
    citation_label: str
    source_status: str
    #: "Version 3 (committed)" or "Working draft — not committed".
    content_label: str
    blocks: tuple[RenderBlock, ...] = ()
    checklist: tuple[ChecklistLine, ...] = ()
    #: Printed instead of prose when the section has none. The renderers never
    #: substitute text of their own for an empty section.
    empty_note: str | None = None


@dataclass(frozen=True)
class AttachmentLine:
    kind_title: str
    title: str
    sha256_prefix: str
    uploaded_on: date
    withdrawn: bool = False


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class IcaapDocument:
    """Everything one ICAAP artifact prints, already resolved and formatted.

    ``generated_at`` is supplied by the caller and must be pinned for the
    export to be reproducible: it is the only wall-clock value on the page, and
    both renderers stamp it into the document metadata as well as the footer.
    """

    institution_name: str
    institution_short_name: str
    cycle_title: str
    fiscal_year: int
    as_of: date
    #: Stored value (``solo``/``consolidated``) — used in the filename only.
    basis: str
    #: Display wording for the same thing. Nothing prints the stored value.
    basis_label: str
    cycle_kind: str
    cycle_kind_label: str
    framework_code: str
    framework_title: str
    framework_version: str
    framework_status: str
    #: The digest of the framework text this document was actually rendered
    #: from — not the one the cycle recorded, which may differ (see
    #: :attr:`framework_digest_note`).
    framework_digest: str
    regulator_short: str
    currency: str
    generated_at: datetime
    sections: tuple[SectionRender, ...] = ()
    blocks: Mapping[str, BlockRender] = field(default_factory=dict)
    #: ``(block_id, fact_key) -> formatted value``. A pair that is absent, or
    #: whose fact has no value, renders :data:`NOT_AVAILABLE`.
    fact_text: Mapping[tuple[str, str], str] = field(default_factory=dict)
    attachments: tuple[AttachmentLine, ...] = ()
    #: Set only when the framework file has changed since the cycle recorded
    #: its digest — a version published over itself. The reader is told, because
    #: the requirements they are reading are then not the ones the cycle was
    #: built against.
    framework_digest_note: str | None = None
    #: Draft-only: the readiness counts and pending sections, in plain lines.
    readiness_summary: tuple[str, ...] = ()
    #: :data:`DRAFT_WATERMARK` for drafts; ``None`` for the P3 filing PDF.
    watermark: str | None = DRAFT_WATERMARK
    #: D-029: adds :data:`REHEARSAL_WATERMARK` alongside whatever else is set.
    is_rehearsal: bool = False

    @property
    def watermark_lines(self) -> tuple[str, ...]:
        """Every watermark this document carries, in printing order."""
        lines: list[str] = []
        if self.watermark:
            lines.append(self.watermark)
        if self.is_rehearsal:
            lines.append(REHEARSAL_WATERMARK)
        return tuple(lines)

    @property
    def is_exposure_draft(self) -> bool:
        """The regulator has not finalised the framework this document follows."""
        return self.framework_status == EXPOSURE_DRAFT_STATUS

    def fact(self, block_id: str, fact_key: str) -> str:
        """The formatted value for one ``factRef``, or :data:`NOT_AVAILABLE`."""
        return self.fact_text.get((block_id, fact_key)) or NOT_AVAILABLE

    def block(self, block_id: str) -> BlockRender | None:
        """The bound block a ``dataBlock`` node points at, if the cycle has one."""
        return self.blocks.get(block_id)


__all__ = [
    "COMMITTED_CONTENT_LABEL",
    "DRAFT_PROVENANCE_NOTICE",
    "DRAFT_WATERMARK",
    "EXPOSURE_DRAFT_NOTICE",
    "EXPOSURE_DRAFT_STATUS",
    "NOT_AVAILABLE",
    "NO_COMMITTED_TEXT",
    "NO_COMMITTED_VERSION_LABEL",
    "NO_TEXT_YET",
    "PARAGRAPH_STYLES",
    "REHEARSAL_WATERMARK",
    "WORKING_CONTENT_LABEL",
    "WORKING_COPY_NOTICE",
    "AttachmentLine",
    "BlockRender",
    "ChecklistLine",
    "DataBlockRef",
    "FactRef",
    "IcaapDocument",
    "ListBlock",
    "ParagraphBlock",
    "ParagraphStyle",
    "QuoteBlock",
    "RenderBlock",
    "Run",
    "SectionRender",
    "TableColumn",
    "TableRow",
    "TableSpec",
]
