"""Building — and streaming — the ICAAP draft PDF and DOCX.

This is the seam between the workspace and the page. Everything the renderers
print is gathered here, once, from the same services the screen reads
(``sections``, ``blocks``, ``attachments``, ``readiness``), so a draft cannot
show a figure the workspace does not show. P3's filing exports build the same
:class:`~app.domain.icaap.document.IcaapDocument` from the frozen snapshot
instead; the renderers never learn which one they are printing.

Three deliberate properties:

* **Drafts are never stored.** The bytes stream straight to the caller. They
  are not an artifact, carry no version, and need no object storage — which
  also means a deployment without storage can still read its own ICAAP.
* **Nothing is computed here.** Figures come from block bindings as they were
  captured; a fact with no value prints "Not available"; a requirement's
  not-applicable reason travels with the document. No threshold, floor or
  total is written by this module (founder directive D-024).
* **A rehearsal exports like anything else** (D-029) and is watermarked as a
  rehearsal. Rehearsals never mint a package, so the draft is the only
  document they produce.

``content="working"`` prints each section's live working text, labelled as
uncommitted. ``content="committed"`` prints the latest committed version and
says so where a section has none — it never falls back to the working text,
because the whole point of asking for the committed view is to see what has
actually been signed off internally.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.icaap.document import (
    COMMITTED_CONTENT_LABEL,
    DRAFT_WATERMARK,
    NO_COMMITTED_TEXT,
    NO_COMMITTED_VERSION_LABEL,
    NO_TEXT_YET,
    WORKING_CONTENT_LABEL,
    AttachmentLine,
    BlockRender,
    ChecklistLine,
    IcaapDocument,
    RenderBlock,
    SectionRender,
)
from app.models.icaap import (
    IcaapBlockBinding,
    IcaapCycle,
    IcaapDataBlock,
    IcaapSection,
    IcaapSectionVersion,
)
from app.services.icaap.render import format as fmt

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.api.deps import IcaapAccess
    from app.domain.icaap.frameworks.schema import Citation, Framework, SectionDef

ContentMode = Literal["working", "committed"]

#: Audit event recorded for every draft that leaves the server.
EXPORT_EVENT = "icaap.draft.exported"

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: Readiness severities, in the order the cover lists them.
_SEVERITY_LABELS: Mapping[str, str] = {
    "blocking": "must be resolved before freeze",
    "warning": "to review",
    "info": "for information",
}


# --------------------------------------------------------------------------
# Framework helpers
# --------------------------------------------------------------------------
def _document_short_label(framework: Framework, doc_id: str) -> str:
    for document in framework.documents:
        if document.id == doc_id:
            return document.short_label
    for instrument in framework.related_instruments:
        if instrument.id == doc_id:
            return instrument.title
    return doc_id


def citation_label(framework: Framework, citation: Citation | None) -> str:
    """ "ICAAP Guideline ¶49(a)" — the reference a reader can look up.

    The document's short label comes from the framework JSON, so no regulator
    or instrument name is written in Python.
    """
    if citation is None:
        return ""
    return f"{_document_short_label(framework, citation.doc)} ¶{citation.ref}"


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------
def _section_rows(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> dict[str, IcaapSection]:
    rows = db.scalars(
        select(IcaapSection).where(
            IcaapSection.organization_id == access.ctx.organization_id,
            IcaapSection.bank_id == access.bank.id,
            IcaapSection.cycle_id == cycle.id,
        )
    ).all()
    return {row.section_key: row for row in rows}


def _committed_versions(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, sections: Mapping[str, IcaapSection]
) -> dict[str, IcaapSectionVersion]:
    wanted = {
        key: row.committed_version_no
        for key, row in sections.items()
        if row.committed_version_no is not None
    }
    if not wanted:
        return {}
    rows = db.scalars(
        select(IcaapSectionVersion).where(
            IcaapSectionVersion.organization_id == access.ctx.organization_id,
            IcaapSectionVersion.bank_id == access.bank.id,
            IcaapSectionVersion.cycle_id == cycle.id,
            IcaapSectionVersion.section_key.in_(wanted),
        )
    ).all()
    latest: dict[str, IcaapSectionVersion] = {}
    for row in rows:
        if row.version_no == wanted.get(row.section_key):
            latest[row.section_key] = row
    return latest


def _checklist(
    definition: SectionDef, framework: Framework, state: Mapping[str, Any]
) -> tuple[ChecklistLine, ...]:
    lines: list[ChecklistLine] = []
    for item in definition.requirements:
        recorded = state.get(item.id) if isinstance(state, Mapping) else None
        status = "open"
        reason: str | None = None
        if isinstance(recorded, Mapping):
            status = str(recorded.get("status") or "open")
            raw_reason = recorded.get("reason")
            reason = None if raw_reason is None else str(raw_reason)
        lines.append(
            ChecklistLine(
                item_id=item.id,
                text=item.text,
                citation_label=citation_label(
                    framework, item.citations[0] if item.citations else None
                ),
                status=status,
                reason=reason,
            )
        )
    return tuple(lines)


def _section_renders(
    *,
    framework: Framework,
    sections: Mapping[str, IcaapSection],
    committed: Mapping[str, IcaapSectionVersion],
    content: ContentMode,
) -> tuple[SectionRender, ...]:
    from app.domain.icaap import prosemirror  # noqa: PLC0415 - pure domain, imported lazily

    renders: list[SectionRender] = []
    for definition in sorted(framework.sections, key=lambda section: section.order):
        row = sections.get(definition.key)
        version = committed.get(definition.key)
        doc: Mapping[str, Any] | None
        if content == "committed":
            doc = version.doc if version is not None else None
            label = (
                COMMITTED_CONTENT_LABEL.format(version=version.version_no)
                if version is not None
                else NO_COMMITTED_VERSION_LABEL
            )
            empty_note = NO_COMMITTED_TEXT
        else:
            doc = row.working_doc if row is not None else None
            label = fmt.content_label(
                committed_version=row.committed_version_no if row is not None else None
            )
            if row is not None and row.committed_version_no is not None:
                # The working text is what is printed, so say that plainly even
                # when a committed version exists.
                label = WORKING_CONTENT_LABEL
            empty_note = NO_TEXT_YET
        blocks: tuple[RenderBlock, ...] = ()
        if isinstance(doc, Mapping) and doc:
            blocks = prosemirror.to_render_blocks(doc)
        renders.append(
            SectionRender(
                key=definition.key,
                letter=definition.letter,
                title=definition.title,
                citation_label=citation_label(framework, definition.citation),
                source_status=definition.source_status,
                content_label=label,
                blocks=blocks,
                checklist=_checklist(
                    definition, framework, row.checklist_state if row is not None else {}
                ),
                empty_note=None if blocks else empty_note,
            )
        )
    return tuple(renders)


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------
def _block_title(block_type: str, stored_title: str | None) -> str:
    if stored_title:
        return stored_title
    from app.domain.icaap.blocks import BLOCK_CATALOGUE  # noqa: PLC0415 - pure domain

    spec = BLOCK_CATALOGUE.get(block_type)
    return spec.title if spec is not None else fmt.humanize(block_type)


def _binding_provenance(binding: IcaapBlockBinding) -> tuple[tuple[str, str], ...]:
    """What the provenance page says about one bound block.

    Only identifiers the resolver recorded — run ids, input hashes, package and
    sign-off versions — so a reader can go back to the sealed row this figure
    came from.
    """
    lines: list[tuple[str, str]] = [("Source", binding.source_key)]
    reference = binding.source_ref if isinstance(binding.source_ref, Mapping) else {}
    for key in ("run_id", "input_hash", "engine_version", "package_id", "signoff_id", "plan_id"):
        value = reference.get(key)
        if value:
            lines.append((fmt.humanize(key), str(value)))
    if binding.source_run_ids:
        lines.append(("Runs", ", ".join(str(run) for run in binding.source_run_ids)))
    return tuple(lines)


def _block_titles(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> dict[str, str | None]:
    """The title the author gave each block, where they gave one.

    A manual table the user named "Five-year summary" must print under that
    name, not under the catalogue's generic one — including before it is bound,
    when there is no payload to take the title from.
    """
    rows = db.execute(
        select(IcaapDataBlock.id, IcaapDataBlock.title).where(
            IcaapDataBlock.organization_id == access.ctx.organization_id,
            IcaapDataBlock.bank_id == access.bank.id,
            IcaapDataBlock.cycle_id == cycle.id,
        )
    ).all()
    return {str(block_id): title for block_id, title in rows}


def _block_renders(
    *,
    block_states: Sequence[Any],
    bindings: Mapping[UUID, IcaapBlockBinding],
    titles: Mapping[str, str | None],
) -> dict[str, BlockRender]:
    # Block states key by the string id the editor's ``blockId`` attribute
    # carries; bindings come back keyed by UUID. Normalising to the string form
    # once is what lets a ``dataBlock`` node find its figures.
    by_id = {str(block_id): binding for block_id, binding in bindings.items()}
    renders: dict[str, BlockRender] = {}
    for state in block_states:
        key = str(state.block_id)
        binding = by_id.get(key)
        renders[key] = fmt.block_render(
            block_id=key,
            payload=binding.payload if binding is not None else None,
            status=str(state.status),
            pin_reason=state.pin_reason,
            fallback_title=_block_title(state.block_type, titles.get(key)),
            source_as_of=binding.source_as_of if binding is not None else None,
            provenance=_binding_provenance(binding) if binding is not None else (),
        )
    return renders


# --------------------------------------------------------------------------
# Readiness summary
# --------------------------------------------------------------------------
def _readiness_lines(
    db: Session, access: IcaapAccess, cycle_id: UUID, *, regulator_short: str
) -> tuple[str, ...]:
    """The cover's readiness block, or an honest line saying why there is none.

    Readiness is advisory decoration on a draft: it tells the author what still
    blocks a freeze. When it cannot be computed — typically a governed
    parameter with no approved row, which fails closed by design (D-024) — the
    refusal is printed rather than propagated. Refusing to render the whole
    document would leave the author unable to read their own ICAAP because the
    *deadline warning window* is unconfigured, which is a fail-closed applied to
    the wrong artifact. Nothing is substituted: the gap is named. The readiness
    endpoint and the P3 freeze still refuse.
    """
    from fastapi import HTTPException, status  # noqa: PLC0415 - one narrow catch

    from app.services.icaap import readiness  # noqa: PLC0415 - avoid a service cycle

    try:
        report = readiness.get_readiness(db, access, cycle_id)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_409_CONFLICT:
            raise
        return (f"Readiness could not be assessed for this draft. {_refusal_text(exc)}",)
    from app.services.icaap import guards  # noqa: PLC0415 - avoid a service cycle

    framework, _ = guards.framework_for(guards.get_cycle_or_404(db, access, cycle_id))
    titles = {section.key: section.title for section in framework.sections}
    return _readiness_summary(report, regulator_short=regulator_short, section_titles=titles)


def _refusal_text(exc: Any) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, Mapping):
        message = detail.get("message")
        if message:
            return str(message)
    return str(detail or "")


def _readiness_summary(
    report: Any, *, regulator_short: str, section_titles: Mapping[str, str]
) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for item in report.items:
        counts[item.severity] = counts.get(item.severity, 0) + 1
    lines = [
        f"{counts[severity]} {label}"
        for severity, label in _SEVERITY_LABELS.items()
        if counts.get(severity)
    ]
    if not lines:
        lines.append("No outstanding readiness items.")
    if report.pending_primary_text_sections:
        lines.append(
            # The section's own TITLE, never its key. A cover page that reads
            # "(business_model_strategy)" hands an internal identifier to a
            # Board and a supervisor (audit R-1, the same class as M10). The
            # key stays the API's stable identifier; only the print changes.
            f"Sections awaiting {regulator_short} text: "
            + ", ".join(
                section_titles.get(key, key) for key in report.pending_primary_text_sections
            )
        )
    if report.deadline.due_date is not None:
        lines.append(f"Due {fmt.format_date(report.deadline.due_date)}.")
    return tuple(lines)


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------
def build_draft_document(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    *,
    content: ContentMode = "working",
    generated_at: datetime,
) -> IcaapDocument:
    """Everything one cycle's draft prints, gathered from the live workspace."""
    from app.services import jurisdictions  # noqa: PLC0415 - avoid a service import cycle
    from app.services.icaap import attachments, blocks, guards  # noqa: PLC0415

    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework, digest_matches = guards.framework_for(cycle)
    bank = access.bank

    section_rows = _section_rows(db, access, cycle)
    committed = _committed_versions(db, access, cycle, section_rows)
    sections = _section_renders(
        framework=framework, sections=section_rows, committed=committed, content=content
    )

    bindings = blocks.current_bindings(db, access, cycle)
    block_states = blocks.block_states(db, access, cycle)
    block_renders = _block_renders(
        block_states=block_states,
        bindings=bindings,
        titles=_block_titles(db, access, cycle),
    )

    currency = jurisdictions.base_currency(bank)
    fact_text = fmt.fact_text_map(
        {str(block_id): binding.facts for block_id, binding in bindings.items()},
        fallback_currency=currency,
    )

    attachment_lines = tuple(
        AttachmentLine(
            kind_title=_attachment_kind_title(framework, attachment.kind),
            title=attachment.title,
            sha256_prefix=attachment.sha256[:16],
            uploaded_on=_as_date(attachment.created_at),
            withdrawn=attachment.withdrawn,
        )
        for attachment in attachments.list_attachments(db, access, cycle_id).attachments
    )

    regulator_short = jurisdictions.regulator_short(db, bank)

    return IcaapDocument(
        institution_name=bank.name,
        institution_short_name=bank.short_name,
        cycle_title=cycle.title,
        fiscal_year=cycle.fiscal_year,
        as_of=cycle.as_of_date,
        basis=cycle.basis,
        basis_label=fmt.basis_label(cycle.basis),
        cycle_kind=cycle.cycle_kind,
        cycle_kind_label=fmt.cycle_kind_label(cycle.cycle_kind),
        framework_code=framework.code,
        framework_title=framework.title,
        framework_version=framework.version,
        framework_status=framework.status,
        framework_digest=framework.digest,
        framework_digest_note=_digest_note(cycle, framework, matches=digest_matches),
        regulator_short=regulator_short,
        currency=currency,
        generated_at=generated_at,
        sections=sections,
        blocks=block_renders,
        fact_text=fact_text,
        attachments=attachment_lines,
        readiness_summary=_readiness_lines(db, access, cycle_id, regulator_short=regulator_short),
        watermark=DRAFT_WATERMARK,
        is_rehearsal=cycle.cycle_kind == "rehearsal",
    )


def _digest_note(cycle: IcaapCycle, framework: Framework, *, matches: bool) -> str | None:
    """Said only when the framework text has moved under the cycle.

    The document renders the framework the registry holds now; the cycle
    recorded a different digest for the same code and version. The reader is
    told which is which, because the requirements printed here are then not the
    ones the cycle was built against.
    """
    if matches:
        return None
    return (
        f"This cycle recorded framework digest {cycle.framework_sha256}; this export renders "
        f"{framework.digest}. The framework text has changed under the same version."
    )


def _attachment_kind_title(framework: Framework, kind: str) -> str:
    for requirement in framework.attachments:
        if requirement.kind == kind:
            return requirement.title
    return fmt.humanize(kind)


def _as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


# --------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------
def _record_export(  # noqa: PLR0913 - the whole audited fact: who, which cycle, which artifact
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    *,
    kind: str,
    content: ContentMode,
    payload: bytes,
) -> None:
    from app.services import audit  # noqa: PLC0415 - avoid a service import cycle

    audit.record_event(
        db,
        access.ctx,
        event_type=EXPORT_EVENT,
        entity_type="icaap_cycle",
        entity_id=cycle_id,
        details={
            "kind": kind,
            "content": content,
            "byte_size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    db.commit()


def export_draft_pdf(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    *,
    content: ContentMode = "working",
    generated_at: datetime | None = None,
) -> tuple[bytes, str]:
    """``(bytes, filename)`` for the draft PDF. Nothing is stored."""
    from app.services.icaap.render.pdf import render_pdf  # noqa: PLC0415 - reportlab is heavy

    document = build_draft_document(
        db, access, cycle_id, content=content, generated_at=generated_at or _now()
    )
    payload = render_pdf(document)
    _record_export(db, access, cycle_id, kind="pdf", content=content, payload=payload)
    return payload, fmt.draft_filename(
        institution_short_name=document.institution_short_name,
        fiscal_year=document.fiscal_year,
        basis=document.basis,
        extension="pdf",
    )


def export_draft_docx(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    *,
    content: ContentMode = "working",
    generated_at: datetime | None = None,
) -> tuple[bytes, str]:
    """``(bytes, filename)`` for the editable working copy. Nothing is stored."""
    from app.services.icaap.render.docx import render_docx  # noqa: PLC0415 - python-docx is heavy

    document = build_draft_document(
        db, access, cycle_id, content=content, generated_at=generated_at or _now()
    )
    payload = render_docx(document)
    _record_export(db, access, cycle_id, kind="docx", content=content, payload=payload)
    return payload, fmt.draft_filename(
        institution_short_name=document.institution_short_name,
        fiscal_year=document.fiscal_year,
        basis=document.basis,
        extension="docx",
    )


def _now() -> datetime:
    from datetime import UTC  # noqa: PLC0415 - kept beside its single use

    return datetime.now(UTC).replace(microsecond=0)


__all__ = [
    "DOCX_MEDIA_TYPE",
    "EXPORT_EVENT",
    "PDF_MEDIA_TYPE",
    "ContentMode",
    "build_draft_document",
    "citation_label",
    "export_draft_docx",
    "export_draft_pdf",
]
