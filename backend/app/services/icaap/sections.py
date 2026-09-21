"""ICAAP section text: autosave with optimistic concurrency, and immutable versions.

Two people drafting the same section in two tabs is the normal case, not the
edge case, so every save carries the revision it was based on and the update is
conditional on it. A stale save is refused with the current revision and who
last touched it; it is never silently applied over someone else's paragraph.

Committing is the act that matters. It writes an immutable version with the
canonical document, its plain text, its digest and the figures it quoted, and
that is what a reviewer reads and what P3's freeze seals. Autosaves write no
audit event on purpose — the audit log is append-only and a per-keystroke
event would bury the decisions in it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.domain.icaap import prosemirror
from app.domain.icaap.frameworks.rebase import RebasePlan
from app.domain.icaap.frameworks.schema import Framework, SectionDef
from app.domain.icaap.readiness import ItemState, SectionState
from app.models.icaap import IcaapCycle, IcaapDataBlock, IcaapSection, IcaapSectionVersion
from app.schemas.icaap import (
    IcaapRequirementRead,
    IcaapRequirementStateUpdate,
    IcaapSectionCommit,
    IcaapSectionListRead,
    IcaapSectionRead,
    IcaapSectionSummaryRead,
    IcaapSectionVersionListRead,
    IcaapSectionVersionRead,
    IcaapSectionVersionSummaryRead,
    IcaapSectionWorkingSave,
)
from app.services.audit import record_event
from app.services.icaap import ai_provenance as ai_provenance_service
from app.services.icaap import guards, serializers

_MIN_WAIVER_REASON = 10


def _rows(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> list[IcaapSection]:
    return list(
        db.scalars(
            select(IcaapSection)
            .where(
                IcaapSection.organization_id == access.ctx.organization_id,
                IcaapSection.cycle_id == cycle.id,
            )
            .order_by(IcaapSection.position)
        )
    )


def _row_or_404(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, section_key: str
) -> IcaapSection:
    row = db.scalar(
        select(IcaapSection).where(
            IcaapSection.organization_id == access.ctx.organization_id,
            IcaapSection.cycle_id == cycle.id,
            IcaapSection.section_key == section_key,
        )
    )
    if row is None:
        guards.not_found()
    return row


def _state_for(row: IcaapSection, item_id: str) -> dict[str, Any]:
    entry = (row.checklist_state or {}).get(item_id)
    return entry if isinstance(entry, dict) else {}


def _counts(section: SectionDef, row: IcaapSection, conditions: frozenset[str]) -> dict[str, int]:
    tally = {"met": 0, "not_applicable": 0, "auto_not_applicable": 0, "open": 0, "total": 0}
    for item in section.requirements:
        tally["total"] += 1
        if item.applies_when is not None and item.applies_when not in conditions:
            tally["auto_not_applicable"] += 1
            continue
        status = str(_state_for(row, item.id).get("status", "open"))
        tally[status if status in tally else "open"] += 1
    return tally


def _has_uncommitted(row: IcaapSection) -> bool:
    return row.committed_from_rev is None or row.committed_from_rev != row.working_rev


def _summary(
    section: SectionDef, row: IcaapSection, conditions: frozenset[str]
) -> IcaapSectionSummaryRead:
    return IcaapSectionSummaryRead(
        key=section.key,
        letter=section.letter,
        order=section.order,
        title=section.title,
        source_status=section.source_status,
        committed_version_no=row.committed_version_no,
        has_uncommitted_changes=_has_uncommitted(row),
        requirement_counts=_counts(section, row, conditions),
        working_updated_at=row.working_updated_at,
    )


def section_summaries(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, framework: Framework
) -> list[IcaapSectionSummaryRead]:
    from app.services.icaap import cycles as cycles_service  # noqa: PLC0415 - mutual read

    conditions = cycles_service.cycle_conditions(db, access, cycle)
    rows = {row.section_key: row for row in _rows(db, access, cycle)}
    return [
        _summary(section, rows[section.key], conditions)
        for section in framework.sections
        if section.key in rows
    ]


def list_sections(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapSectionListRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    return IcaapSectionListRead(sections=section_summaries(db, access, cycle, framework))


def _requirements(  # noqa: PLR0913 - the render context is six explicit parts
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    framework: Framework,
    section: SectionDef,
    row: IcaapSection,
) -> list[IcaapRequirementRead]:
    from app.services.icaap import cycles as cycles_service  # noqa: PLC0415 - mutual read

    conditions = cycles_service.cycle_conditions(db, access, cycle)
    codes = frozenset(code for item in section.requirements for code in item.param_refs)
    values, pending = serializers.resolve_param_values(db, access.bank, codes)
    out: list[IcaapRequirementRead] = []
    for item in section.requirements:
        state = _state_for(row, item.id)
        applicable = item.applies_when is None or item.applies_when in conditions
        out.append(
            IcaapRequirementRead(
                item=serializers.requirement_item_read(framework, item),
                status=str(state.get("status", "open")),  # pyright: ignore[reportArgumentType]
                reason=state.get("reason"),
                applicable=applicable,
                updated_by=state.get("updated_by"),
                updated_at=state.get("updated_at"),
                resolved_text=serializers.render_requirement_text(item, values, pending),
            )
        )
    return out


def get_section(
    db: Session, access: IcaapAccess, cycle_id: UUID, section_key: str
) -> IcaapSectionRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    if not framework.has_section(section_key):
        guards.not_found()
    section = framework.section(section_key)
    row = _row_or_404(db, access, cycle, section_key)
    from app.services.icaap import cycles as cycles_service  # noqa: PLC0415 - mutual read

    conditions = cycles_service.cycle_conditions(db, access, cycle)
    summary = _summary(section, row, conditions)
    return IcaapSectionRead(
        **summary.model_dump(),
        cycle_id=cycle.id,
        citation=serializers.citation_read(framework, section.citation),
        guidance=section.guidance,
        working_doc=row.working_doc,
        working_rev=row.working_rev,
        working_updated_by=row.working_updated_by,
        requirements=_requirements(db, access, cycle, framework, section, row),
        expected_block_types=list(section.data_blocks),
        editable=cycle.status in guards.EDITABLE_STATUSES,
        carried_from=row.carried_from,
    )


def _validate_ai_markers(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, doc: Mapping[str, Any]
) -> None:
    """An "AI-drafted" badge must name a suggestion somebody actually accepted.

    Without this the marker would be a claim the editor could forge, in either
    direction — a bank might want AI text to read as human-written as readily as
    the reverse. The unalterable decision log is the authority; this keeps the
    document's own markers honest against it.
    """
    from app.services.icaap import ai_provenance  # noqa: PLC0415 - avoid a cycle

    unknown = ai_provenance.unknown_markers(db, access, cycle, doc)
    if unknown:
        raise guards.unprocessable(
            "unknown_ai_suggestion",
            "This section is marked as AI-drafted from a draft that is not part "
            "of this ICAAP.",
            suggestion_ids=list(unknown),
        )


def _validate_refs(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, doc: Mapping[str, Any]
) -> None:
    """Every embedded block and quoted figure must exist and still be live."""
    _validate_ai_markers(db, access, cycle, doc)
    refs = prosemirror.collect_refs(doc)
    if not refs.block_ids:
        return
    blocks = {
        str(block.id): block
        for block in db.scalars(
            select(IcaapDataBlock).where(
                IcaapDataBlock.organization_id == access.ctx.organization_id,
                IcaapDataBlock.cycle_id == cycle.id,
            )
        )
    }
    for block_id in sorted(refs.block_ids):
        block = blocks.get(block_id)
        if block is None:
            raise guards.unprocessable(
                "unknown_block",
                "This section refers to a figure block that is not part of this ICAAP.",
                block_id=block_id,
            )
        if block.retired_at is not None:
            raise guards.unprocessable(
                "retired_block",
                "This section refers to a figure block that has been retired.",
                block_id=block_id,
            )
    from app.services.icaap import blocks as blocks_service  # noqa: PLC0415 - mutual read

    for ref in refs.fact_refs:
        block = blocks[ref.block_id]
        if not blocks_service.block_has_fact(db, access, block, ref.fact_key):
            raise guards.unprocessable(
                "unknown_fact",
                "This section quotes a figure that block does not publish.",
                block_id=ref.block_id,
                fact_key=ref.fact_key,
            )


def _validate_doc(payload_doc: object) -> dict[str, Any]:
    try:
        return prosemirror.validate_doc(payload_doc)
    except prosemirror.ProseMirrorValidationError as exc:
        raise guards.unprocessable(
            "invalid_document",
            "The editor sent content this section cannot hold.",
            code=exc.code,
            path=exc.path,
        ) from exc


def save_working(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    section_key: str,
    payload: IcaapSectionWorkingSave,
) -> IcaapSectionRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    if not framework.has_section(section_key):
        guards.not_found()
    row = _row_or_404(db, access, cycle, section_key)
    doc = _validate_doc(payload.doc)
    _validate_refs(db, access, cycle, doc)

    now = utc_now()
    # ``CursorResult`` carries rowcount; the base ``Result`` type does not.
    result = cast(
        "CursorResult[Any]",
        db.execute(
            update(IcaapSection)
            .where(
                IcaapSection.id == row.id,
                IcaapSection.organization_id == access.ctx.organization_id,
                IcaapSection.working_rev == payload.base_rev,
            )
            .values(
                working_doc=doc,
                working_rev=IcaapSection.working_rev + 1,
                working_updated_by=guards.actor_id(access),
                working_updated_at=now,
                updated_at=now,
            )
        ),
    )
    if result.rowcount != 1:
        db.rollback()
        current = _row_or_404(db, access, cycle, section_key)
        raise guards.conflict(
            "section_rev_conflict",
            "Someone else saved this section while you were editing. Reload to see "
            "their version before saving yours.",
            current_rev=current.working_rev,
            updated_by=str(current.working_updated_by) if current.working_updated_by else None,
            updated_at=(
                current.working_updated_at.isoformat() if current.working_updated_at else None
            ),
        )
    db.commit()
    return get_section(db, access, cycle_id, section_key)


def commit_version(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    section_key: str,
    payload: IcaapSectionCommit,
) -> IcaapSectionVersionRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    if not framework.has_section(section_key):
        guards.not_found()
    row = _row_or_404(db, access, cycle, section_key)
    if row.working_rev != payload.base_rev:
        raise guards.conflict(
            "section_rev_conflict",
            "This section changed after you opened it. Reload and commit what you "
            "have actually read.",
            current_rev=row.working_rev,
        )
    doc = _validate_doc(row.working_doc)
    _validate_refs(db, access, cycle, doc)
    if not prosemirror.has_content(doc):
        raise guards.unprocessable(
            "section_empty", "There is nothing to commit in this section yet."
        )
    digest = prosemirror.canonical_digest(doc)
    latest = db.scalar(
        select(IcaapSectionVersion)
        .where(
            IcaapSectionVersion.organization_id == access.ctx.organization_id,
            IcaapSectionVersion.section_id == row.id,
        )
        .order_by(IcaapSectionVersion.version_no.desc())
        .limit(1)
    )
    if latest is not None and latest.doc_sha256 == digest:
        raise guards.conflict(
            "no_changes",
            "This section is unchanged since the last committed version.",
            version_no=latest.version_no,
        )
    refs = prosemirror.collect_refs(doc)
    version = IcaapSectionVersion(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        section_id=row.id,
        section_key=section_key,
        version_no=(latest.version_no + 1) if latest is not None else 1,
        round=cycle.round,
        source_rev=row.working_rev,
        editor_schema_version=prosemirror.EDITOR_SCHEMA_VERSION,
        doc=doc,
        plain_text=prosemirror.plain_text(doc),
        doc_sha256=digest,
        fact_refs=[
            {
                "block_id": ref.block_id,
                "fact_key": ref.fact_key,
                "suggestion_id": ref.suggestion_id,
            }
            for ref in refs.fact_refs
        ],
        block_refs=sorted(refs.block_ids),
        commit_note=payload.note,
        ai_provenance=ai_provenance_service.provenance_for_doc(db, access, cycle, doc),
        committed_by=guards.actor_id(access),
    )
    db.add(version)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise guards.conflict(
            "version_conflict",
            "Another version of this section was committed at the same moment. "
            "Reload and try again.",
        ) from exc
    row.committed_version_no = version.version_no
    row.committed_from_rev = row.working_rev
    record_event(
        db,
        access.ctx,
        event_type="icaap.section.version_committed",
        entity_type="icaap_section_version",
        entity_id=version.id,
        details={
            "cycle_id": str(cycle.id),
            "section_key": section_key,
            "version_no": version.version_no,
            "doc_sha256": digest,
            "blocks": version.block_refs,
            "note": payload.note,
        },
    )
    db.commit()
    db.refresh(version)
    return _version_read(version)


def _version_summary(version: IcaapSectionVersion) -> IcaapSectionVersionSummaryRead:
    return IcaapSectionVersionSummaryRead(
        id=version.id,
        section_key=version.section_key,
        version_no=version.version_no,
        round=version.round,
        doc_sha256=version.doc_sha256,
        commit_note=version.commit_note,
        committed_by=version.committed_by,
        created_at=version.created_at,
    )


def _version_read(version: IcaapSectionVersion) -> IcaapSectionVersionRead:
    return IcaapSectionVersionRead(
        **_version_summary(version).model_dump(),
        doc=version.doc,
        plain_text=version.plain_text,
        fact_refs=list(version.fact_refs or []),
        block_refs=list(version.block_refs or []),
        editor_schema_version=version.editor_schema_version,
        ai_provenance=version.ai_provenance,
    )


def list_versions(
    db: Session, access: IcaapAccess, cycle_id: UUID, section_key: str
) -> IcaapSectionVersionListRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    row = _row_or_404(db, access, cycle, section_key)
    versions = db.scalars(
        select(IcaapSectionVersion)
        .where(
            IcaapSectionVersion.organization_id == access.ctx.organization_id,
            IcaapSectionVersion.section_id == row.id,
        )
        .order_by(IcaapSectionVersion.version_no.desc())
    ).all()
    return IcaapSectionVersionListRead(versions=[_version_summary(version) for version in versions])


def get_version(
    db: Session, access: IcaapAccess, cycle_id: UUID, section_key: str, version_no: int
) -> IcaapSectionVersionRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    row = _row_or_404(db, access, cycle, section_key)
    version = db.scalar(
        select(IcaapSectionVersion).where(
            IcaapSectionVersion.organization_id == access.ctx.organization_id,
            IcaapSectionVersion.section_id == row.id,
            IcaapSectionVersion.version_no == version_no,
        )
    )
    if version is None:
        guards.not_found()
    return _version_read(version)


def latest_versions(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> dict[str, IcaapSectionVersion]:
    """The newest committed version of each section — what a freeze would seal."""
    latest: dict[str, IcaapSectionVersion] = {}
    for version in db.scalars(
        select(IcaapSectionVersion)
        .where(
            IcaapSectionVersion.organization_id == access.ctx.organization_id,
            IcaapSectionVersion.cycle_id == cycle.id,
        )
        .order_by(IcaapSectionVersion.version_no.asc())
    ):
        latest[version.section_key] = version
    return latest


def set_requirement_state(  # noqa: PLR0913 - the addressed item is five path parts
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    section_key: str,
    item_id: str,
    payload: IcaapRequirementStateUpdate,
) -> IcaapSectionRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    if not framework.has_section(section_key):
        guards.not_found()
    section = framework.section(section_key)
    item = next((entry for entry in section.requirements if entry.id == item_id), None)
    if item is None:
        guards.not_found()
    if payload.status == "not_applicable":
        if not item.waivable:
            raise guards.conflict(
                "not_waivable",
                "The regulator requires this item of every institution; it cannot be "
                "marked not applicable.",
                item_id=item_id,
            )
        # A waiver with no reason is indistinguishable from skipping the work.
        if not payload.reason or len(payload.reason.strip()) < _MIN_WAIVER_REASON:
            raise guards.unprocessable(
                "reason_required",
                "Say why this item does not apply to the institution.",
                item_id=item_id,
            )
    row = _row_or_404(db, access, cycle, section_key)
    state = dict(row.checklist_state or {})
    state[item_id] = {
        "status": payload.status,
        "reason": payload.reason,
        "updated_by": str(guards.actor_id(access)),
        "updated_at": utc_now().isoformat(),
    }
    row.checklist_state = state
    record_event(
        db,
        access.ctx,
        event_type="icaap.requirement.state_changed",
        entity_type="icaap_section",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "section_key": section_key,
            "item_id": item_id,
            "status": payload.status,
            "reason": payload.reason,
        },
    )
    db.commit()
    return get_section(db, access, cycle_id, section_key)


def working_references(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, block_id: str
) -> list[str]:
    """Sections whose WORKING text still embeds or quotes this block.

    Committed versions are immutable history and are deliberately not checked:
    retiring a block must not be blocked by text that has already been sealed,
    and that text keeps its own copy of the figures anyway.
    """
    return [
        row.section_key
        for row in _rows(db, access, cycle)
        if block_id in prosemirror.collect_refs(row.working_doc).block_ids
    ]


def section_states(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, framework: Framework
) -> tuple[SectionState, ...]:
    """The readiness view of every section: text, checklist and the blocks it cites."""
    from app.services.icaap import cycles as cycles_service  # noqa: PLC0415 - mutual read

    conditions = cycles_service.cycle_conditions(db, access, cycle)
    rows = {row.section_key: row for row in _rows(db, access, cycle)}
    versions = latest_versions(db, access, cycle)
    states: list[SectionState] = []
    for section in framework.sections:
        row = rows.get(section.key)
        if row is None:
            continue
        version = versions.get(section.key)
        # What a freeze would seal is the committed text; before anything is
        # committed, the working draft is what the preparer is being asked about.
        doc = version.doc if version is not None else row.working_doc
        item_states: dict[str, ItemState] = {}
        for item in section.requirements:
            if item.applies_when is not None and item.applies_when not in conditions:
                continue
            state = _state_for(row, item.id)
            item_states[item.id] = ItemState(
                status=str(state.get("status", "open")),  # pyright: ignore[reportArgumentType]
                reason=state.get("reason"),
            )
        states.append(
            SectionState(
                key=section.key,
                committed_version_no=row.committed_version_no,
                committed_has_content=(
                    version is not None and prosemirror.has_content(version.doc)
                ),
                uncommitted_changes=_has_uncommitted(row),
                item_states=item_states,
                referenced_block_ids=prosemirror.collect_refs(doc).block_ids,
            )
        )
    return tuple(states)


def carry_sections(  # noqa: PLR0913 - a rebase needs both sides plus the plan
    db: Session,
    access: IcaapAccess,
    *,
    source_cycle: IcaapCycle,
    new_cycle: IcaapCycle,
    target: Framework,
    plan: RebasePlan,
    source_sections: Mapping[str, IcaapSection],
    block_id_map: Mapping[str, str],
) -> list[str]:
    """Create the target cycle's sections, carrying text across the mapping."""
    latest = latest_versions(db, access, source_cycle)
    carried_docs: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    provenance: dict[str, list[dict[str, Any]]] = {}
    for move in plan.moves:
        if move.to_key is None:
            continue
        row = source_sections.get(move.from_key)
        if row is None:
            continue
        version = latest.get(move.from_key)
        doc = version.doc if version is not None else row.working_doc
        remapped = _remap_blocks(doc, block_id_map)
        if not prosemirror.has_content(remapped):
            continue
        carried_docs.setdefault(move.to_key, []).append((move.from_key, remapped))
        provenance.setdefault(move.to_key, []).append(
            {
                "from_section": move.from_key,
                "relation": move.relation,
                "needs_review": move.needs_review,
                "source_version_no": None if version is None else version.version_no,
            }
        )

    carried: list[str] = []
    for section in target.sections:
        parts = carried_docs.get(section.key, [])
        doc = _merge_docs(parts, source_sections)
        row = IcaapSection(
            organization_id=new_cycle.organization_id,
            bank_id=new_cycle.bank_id,
            cycle_id=new_cycle.id,
            section_key=section.key,
            letter=section.letter,
            position=section.order,
            working_doc=doc,
            working_rev=1 if parts else 0,
            working_updated_by=guards.actor_id(access) if parts else None,
            working_updated_at=utc_now() if parts else None,
            checklist_state=_carry_checklist(section, source_sections, plan),
            carried_from=(
                {
                    "sources": provenance[section.key],
                    "needs_review": any(entry["needs_review"] for entry in provenance[section.key]),
                }
                if section.key in provenance
                else None
            ),
        )
        db.add(row)
        if parts:
            carried.append(section.key)
    return carried


def _carry_checklist(
    section: SectionDef, source_sections: Mapping[str, IcaapSection], plan: RebasePlan
) -> dict[str, Any]:
    """Keep the state of requirement ids that exist in both versions."""
    merged: dict[str, Any] = {}
    for row in source_sections.values():
        for item_id, state in (row.checklist_state or {}).items():
            if item_id in plan.carried_items and any(
                item.id == item_id for item in section.requirements
            ):
                merged[item_id] = state
    return merged


def _merge_docs(
    parts: list[tuple[str, dict[str, Any]]], source_sections: Mapping[str, IcaapSection]
) -> dict[str, Any]:
    if not parts:
        return dict(prosemirror.EMPTY_DOC)
    if len(parts) == 1:
        return parts[0][1]
    # Several source sections merge into one: keep each under a heading naming
    # where it came from, so a reviewer can see the seam rather than a blend.
    content: list[dict[str, Any]] = []
    for from_key, doc in parts:
        row = source_sections.get(from_key)
        label = row.section_key if row is not None else from_key
        content.append(
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": f"Carried from {label}"}],
            }
        )
        content.extend(doc.get("content", []) or [])
    return {"type": "doc", "content": content}


def _remap_blocks(doc: Mapping[str, Any], block_id_map: Mapping[str, str]) -> dict[str, Any]:
    """Point carried text at the new cycle's block ids."""

    def walk(node: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = dict(node)
        attrs = out.get("attrs")
        if isinstance(attrs, dict) and "blockId" in attrs:
            attrs = dict(attrs)
            attrs["blockId"] = block_id_map.get(str(attrs["blockId"]), attrs["blockId"])
            out["attrs"] = attrs
        children = out.get("content")
        if isinstance(children, list):
            out["content"] = [walk(child) for child in children]
        return out

    return walk(doc)


__all__ = [
    "carry_sections",
    "commit_version",
    "get_section",
    "get_version",
    "latest_versions",
    "list_sections",
    "list_versions",
    "save_working",
    "section_states",
    "section_summaries",
    "working_references",
    "set_requirement_state",
]
