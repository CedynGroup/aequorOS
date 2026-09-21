"""Requesting, reading, accepting and rejecting an AI draft.

The shape of the feature is in four rules, each enforced here:

1. **Gate at enqueue, and again at run.** The route dependency checks, this
   service checks again inside the transaction, and the job checks a third time
   before it calls anything. A queued request that outlives a switched-off
   feature is cancelled, not sent.
2. **A refusal or a validation failure shows no suggestion, only a status.**
   ``draft`` is populated only for ``validated``; every other state serves the
   status and the codes and nothing else, because the whole point of rejecting
   an ungrounded draft is not putting it in front of somebody.
3. **A human always accepts.** There is no path from a validated suggestion into
   a document that does not go through an explicit accept with chosen paragraph
   indexes, and it goes through P1's ordinary optimistic-save path so a
   concurrent edit still wins its conflict.
4. **Figures stay references.** Accepting converts placeholders into ``factRef``
   nodes bound to the blocks the fact sheet was frozen from — the model's words,
   the platform's numbers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.core.config import get_settings
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.domain.icaap import ai_convert
from app.domain.icaap.blocks import BLOCK_CATALOGUE
from app.domain.icaap.frameworks.schema import SectionDef
from app.models import User
from app.models.icaap import IcaapBlockBinding, IcaapCycle, IcaapSection
from app.models.icaap_ai import IcaapAiSuggestion, IcaapAiSuggestionDecision
from app.schemas.icaap import IcaapSectionRead
from app.schemas.icaap_ai import (
    IcaapAiDecisionRead,
    IcaapAiDraftAccept,
    IcaapAiDraftPreviewRead,
    IcaapAiDraftReject,
    IcaapAiParagraphRead,
    IcaapAiSegmentRead,
    IcaapAiSuggestionListRead,
    IcaapAiSuggestionRead,
)
from app.services import job_queue, jurisdictions
from app.services.ai import gates, observability, quota
from app.services.ai.features import AiFeature
from app.services.attestation.digests import canonical_json, sha256_hex
from app.services.audit import record_event
from app.services.icaap import ai_fact_sheet, ai_prompt, guards, sections
from app.services.icaap import blocks as blocks_service
from app.services.icaap.render import format as fmt

FEATURE: AiFeature = "icaap_drafting"
_ACTIVE_STATUSES = ("queued", "running")


# ---------------------------------------------------------------------------
# quota source: ICAAP registers its own table so a tenant has ONE AI budget
# ---------------------------------------------------------------------------
class _SuggestionUsage:
    def requests_since(
        self, db: Session, organization_id: str, since: datetime, *, user_id: UUID | None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(IcaapAiSuggestion)
            .where(
                IcaapAiSuggestion.organization_id == organization_id,
                IcaapAiSuggestion.created_at >= since,
                # A request the platform refused to send is not spend.
                IcaapAiSuggestion.status != "cancelled",
            )
        )
        if user_id is not None:
            statement = statement.where(IcaapAiSuggestion.requested_by == user_id)
        return int(db.scalar(statement) or 0)

    def output_tokens_since(self, db: Session, organization_id: str, since: datetime) -> int:
        total = 0
        for usage in db.scalars(
            select(IcaapAiSuggestion.usage).where(
                IcaapAiSuggestion.organization_id == organization_id,
                IcaapAiSuggestion.created_at >= since,
            )
        ):
            if isinstance(usage, dict):
                total += int(usage.get("output_tokens") or 0)
        return total


quota.register_source(_SuggestionUsage())


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------
def enqueue(
    db: Session, access: IcaapAccess, cycle_id: UUID, section_key: str
) -> tuple[IcaapAiSuggestionRead, bool]:
    """Freeze a fact sheet and queue one drafting job. Returns (row, created)."""
    settings = get_settings()
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    framework = guards.require_framework(cycle)
    section_def = _section_def_or_404(framework, section_key)
    if not section_def.ai_draftable:
        raise guards.conflict(
            "section_not_ai_draftable",
            "This section is a checklist or a table rather than narrative text, so "
            "it cannot be drafted.",
        )
    row = sections._row_or_404(db, access, cycle, section_key)  # noqa: SLF001 - same package
    actor = guards.actor_id(access)

    # Defence in depth: the dependency already ran this, but a second check
    # inside the transaction is what makes the enqueue and the run symmetric.
    decision = gates.evaluate(
        db,
        access.ctx.organization_id,
        FEATURE,
        phase="enqueue",
        prompt_version=ai_prompt.PROMPT_VERSION,
        settings=settings,
    )
    if not decision.allowed:
        observability.log_ai_event(
            observability.EVENT_GATED,
            feature=FEATURE,
            organization_id=access.ctx.organization_id,
            bank_id=access.bank.id,
            section_key=section_key,
            gate_code=decision.code,
        )
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail={"error_code": decision.code, "message": decision.message},
        )

    existing = _in_flight(db, access, row.id, actor)
    if existing is not None:
        window = timedelta(seconds=settings.ai.enqueue_debounce_seconds)
        if _aware(existing.created_at) > utc_now() - window:
            return read_one(db, access, cycle, existing), False
        raise guards.conflict(
            "ai_draft_already_running",
            "A draft of this section is already being written. Wait for it to finish.",
        )

    allowance = quota.check(db, access.ctx.organization_id, actor, settings=settings)
    if not allowance.allowed:
        raise HTTPException(
            status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error_code": "ai_daily_cap_reached",
                "message": (
                    "Your organisation has reached its daily limit for AI drafts. "
                    "It resets at midnight UTC."
                ),
            },
            headers={"Retry-After": str(allowance.retry_after_seconds)},
        )

    descriptor_only = gates.descriptor_only(db, access.ctx.organization_id)
    try:
        build = _build_sheet(db, access, cycle, row, section_def, descriptor_only=descriptor_only)
    except ai_fact_sheet.NoUsableFactsError as exc:
        raise guards.conflict(
            "no_usable_facts",
            "Link at least one figure block to this section before requesting a draft.",
        ) from exc

    addendum = ai_prompt.section_addendum(framework, section_def)
    metadata = ai_prompt.model_metadata()
    suggestion = IcaapAiSuggestion(
        id=new_uuid7(),
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        cycle_id=cycle.id,
        section_id=row.id,
        section_key=section_key,
        cycle_round=cycle.round,
        status="queued",
        requested_by=actor,
        fact_sheet_mode=build.mode,
        fact_sheet=build.sheet,
        fact_sheet_sha256=build.sha256,
        fact_bindings={fid: record.as_dict() for fid, record in build.bindings.items()},
        entity_keys=list(build.entity_map.keys),
        framework_code=cycle.framework_code,
        framework_version=cycle.framework_version,
        framework_sha256=cycle.framework_sha256,
        prompt_version=ai_prompt.PROMPT_VERSION,
        prompt_sha256=ai_prompt.prompt_digest(addendum),
        model_requested=str(metadata["model_requested"]),
        effort=str(metadata["effort"]),
        max_output_tokens=int(metadata["max_output_tokens"]),  # type: ignore[arg-type]
        fallbacks_mode=str(metadata["fallbacks_mode"]),
        consent_version=settings.ai.consent_version,
        deployment_approval_ref=settings.ai.production_approval_ref,
    )
    db.add(suggestion)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise guards.conflict(
            "ai_draft_already_running",
            "A draft of this section is already being written. Wait for it to finish.",
        ) from exc

    job = job_queue.enqueue(
        db,
        access.ctx.organization_id,
        "icaap_ai_draft",
        bank_id=access.bank.id,
        payload={"suggestion_id": str(suggestion.id)},
        entity_type="icaap_ai_suggestion",
        entity_id=suggestion.id,
        max_attempts=settings.ai.job_max_attempts,
    )
    suggestion.job_id = job.id
    record_event(
        db,
        access.ctx,
        event_type="icaap.ai_draft.requested",
        entity_type="icaap_ai_suggestion",
        entity_id=suggestion.id,
        details={
            "cycle_id": str(cycle.id),
            "section_key": section_key,
            "fact_sheet_sha256": build.sha256,
            "fact_sheet_mode": build.mode,
            "fact_count": build.fact_count,
            "prompt_version": ai_prompt.PROMPT_VERSION,
            "model_requested": str(metadata["model_requested"]),
        },
    )
    observability.log_ai_event(
        observability.EVENT_ENQUEUED,
        feature=FEATURE,
        suggestion_id=str(suggestion.id),
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        cycle_id=str(cycle.id),
        section_key=section_key,
        fact_sheet_sha256=build.sha256,
        fact_sheet_mode=build.mode,
        fact_count=build.fact_count,
        prompt_version=ai_prompt.PROMPT_VERSION,
        model_requested=str(metadata["model_requested"]),
    )
    db.commit()
    db.refresh(suggestion)
    return read_one(db, access, cycle, suggestion), True


def _build_sheet(  # noqa: PLR0913 - the sheet key is five parts plus the mode
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    row: IcaapSection,
    section_def: SectionDef,
    *,
    descriptor_only: bool,
) -> ai_fact_sheet.FactSheetBuild:
    probe = ai_fact_sheet.build_fact_sheet(
        db, access, cycle, row, section_def, descriptor_only=descriptor_only
    )
    limits = ai_fact_sheet.resolve_limits(db, access, cycle, tuple(probe.bindings))
    prior = ai_fact_sheet.collect_prior_year_values(db, access, cycle)
    if not limits and not prior:
        return probe
    return ai_fact_sheet.build_fact_sheet(
        db,
        access,
        cycle,
        row,
        section_def,
        descriptor_only=descriptor_only,
        limits=limits,
        prior_values=prior,
    )


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
def list_drafts(
    db: Session, access: IcaapAccess, cycle_id: UUID, section_key: str
) -> IcaapAiSuggestionListRead:
    """Machine-written proposals for one section — none of them a supervisor's.

    An impersonated examiner reads none of these, whatever the cycle's status.
    A suggestion is working material by definition: the ones a preparer ACCEPTED
    are in the section text the Board approved and the examiner reads them
    there, and the rest are proposals the bank declined or has not looked at.
    A supervisor reading a bank's rejected drafts is reading the working paper,
    which is the boundary ``guards.get_cycle_or_404`` draws for every other read.
    """
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    if access.examiner:
        return IcaapAiSuggestionListRead(items=[])
    rows = db.scalars(
        select(IcaapAiSuggestion)
        .where(
            IcaapAiSuggestion.organization_id == access.ctx.organization_id,
            IcaapAiSuggestion.cycle_id == cycle.id,
            IcaapAiSuggestion.section_key == section_key,
        )
        .order_by(IcaapAiSuggestion.created_at.desc())
    ).all()
    return IcaapAiSuggestionListRead(items=[read_one(db, access, cycle, row) for row in rows])


def get_draft(
    db: Session, access: IcaapAccess, cycle_id: UUID, section_key: str, suggestion_id: UUID
) -> IcaapAiSuggestionRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    if access.examiner:
        # The list serves an examiner nothing, so an id can only have come from
        # somewhere else. 404, the same answer the surface gives for a cycle a
        # supervisor may not read.
        guards.not_found()
    return read_one(
        db, access, cycle, _suggestion_or_404(db, access, cycle, section_key, suggestion_id)
    )


def read_one(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, row: IcaapAiSuggestion
) -> IcaapAiSuggestionRead:
    settings = get_settings()
    decision = db.scalar(
        select(IcaapAiSuggestionDecision).where(
            IcaapAiSuggestionDecision.organization_id == access.ctx.organization_id,
            IcaapAiSuggestionDecision.suggestion_id == row.id,
        )
    )
    bindings = _bindings(row)
    stale = is_stale(db, access, cycle, row)
    return IcaapAiSuggestionRead(
        id=row.id,
        section_key=row.section_key,
        status=row.status,  # type: ignore[arg-type]
        requested_by_name=_display_name(db, row.requested_by),
        created_at=row.created_at,
        completed_at=row.completed_at,
        fact_sheet_mode=row.fact_sheet_mode,  # type: ignore[arg-type]
        fact_count=len((row.fact_sheet or {}).get("facts", [])),
        model_requested=row.model_requested,
        model_served=row.model_served,
        fallback_used=bool(row.fallback_used),
        prompt_version=row.prompt_version,
        failure_code=row.failure_code,
        refusal_category=row.refusal_category,
        validation_error_codes=sorted(
            {
                str(entry.get("code"))
                for entry in (row.validation_errors or [])
                if isinstance(entry, dict)
            }
        ),
        stale=stale,
        decision=(
            IcaapAiDecisionRead(
                decision=decision.decision,  # type: ignore[arg-type]
                paragraph_indexes=[int(index) for index in (decision.paragraph_indexes or [])],
                acknowledged_stale=bool(decision.acknowledged_stale),
                reason=decision.reason,
                decided_at=decision.created_at,
            )
            if decision is not None
            else None
        ),
        poll_after_seconds=settings.ai.client_poll_seconds,
        # ONLY a validated draft is ever served. A refusal or a validation
        # failure shows a status and nothing else.
        draft=_preview(db, access, cycle, row, bindings) if row.status == "validated" else None,
    )


def _preview(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    row: IcaapAiSuggestion,
    bindings: Mapping[str, ai_convert.FactBinding],
) -> IcaapAiDraftPreviewRead | None:
    output = row.output or {}
    paragraphs = output.get("paragraphs") or []
    entity_values = _entity_values(db, access, cycle, row)
    displays = _fact_displays(db, access, cycle, bindings)
    labels = _requirement_labels(cycle)
    rendered: list[IcaapAiParagraphRead] = []
    for index, paragraph in enumerate(paragraphs):
        if not isinstance(paragraph, dict):
            continue
        requirement_ids = [str(value) for value in (paragraph.get("requirement_ids") or [])]
        segments = ai_convert.to_preview_segments(
            str(paragraph.get("text", "")), bindings, entity_values, displays
        )
        rendered.append(
            IcaapAiParagraphRead(
                index=index,
                segments=[
                    IcaapAiSegmentRead(
                        kind=segment.kind,  # type: ignore[arg-type]
                        text=segment.text,
                        block_id=UUID(segment.block_id) if segment.block_id else None,
                        fact_key=segment.fact_key,
                        display=segment.display,
                        status=segment.status,
                    )
                    for segment in segments
                ],
                requirement_ids=requirement_ids,
                requirement_labels=[labels.get(item, item) for item in requirement_ids],
            )
        )
    return IcaapAiDraftPreviewRead(
        paragraphs=rendered,
        open_questions=[str(value) for value in (output.get("open_questions") or [])],
    )


# ---------------------------------------------------------------------------
# accept / reject
# ---------------------------------------------------------------------------
def accept(  # noqa: PLR0913 - the addressed draft is five path parts plus a body
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    section_key: str,
    suggestion_id: UUID,
    payload: IcaapAiDraftAccept,
) -> IcaapSectionRead:
    """Insert the chosen paragraphs into the working document."""
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    row = _suggestion_or_404(db, access, cycle, section_key, suggestion_id)
    if row.status != "validated":
        raise guards.conflict(
            "ai_draft_not_ready",
            "This draft is not available to insert.",
            status=row.status,
        )
    if _decision_for(db, access, row) is not None:
        raise guards.conflict(
            "ai_draft_already_decided",
            "This draft has already been dealt with.",
        )
    # The gates again: a feature switched off between drafting and accepting
    # inserts nothing. The text exists, but the tenant has withdrawn consent to
    # the process that produced it.
    gate = gates.evaluate(
        db,
        access.ctx.organization_id,
        FEATURE,
        phase="enqueue",
        prompt_version=ai_prompt.PROMPT_VERSION,
    )
    if not gate.allowed:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail={"error_code": gate.code, "message": gate.message},
        )

    stale = is_stale(db, access, cycle, row)
    if stale and not payload.acknowledge_stale:
        raise guards.conflict(
            "ai_draft_stale",
            "Figures changed after this draft was written. Review before inserting.",
        )

    paragraphs = [
        paragraph
        for paragraph in (row.output or {}).get("paragraphs", [])
        if isinstance(paragraph, dict)
    ]
    indexes = sorted(set(payload.paragraph_indexes))
    if any(index < 0 or index >= len(paragraphs) for index in indexes):
        raise guards.unprocessable(
            "invalid_paragraph_selection",
            "Some of the selected paragraphs are not part of this draft.",
        )

    bindings = _bindings(row)
    entity_values = _entity_values(db, access, cycle, row)
    nodes = ai_convert.to_paragraph_nodes(
        [str(paragraphs[index].get("text", "")) for index in indexes],
        bindings,
        entity_values,
        str(row.id),
    )
    section_row = sections._row_or_404(db, access, cycle, section_key)  # noqa: SLF001
    merged = ai_convert.append_to_doc(
        section_row.working_doc or {"type": "doc", "content": []}, nodes
    )

    # Through P1's ordinary optimistic-save path: the same revision conflict, the
    # same document validation, the same reference checks. AI text is not a
    # privileged way into a section.
    from app.schemas.icaap import IcaapSectionWorkingSave  # noqa: PLC0415 - schema at use site

    decision_row = IcaapAiSuggestionDecision(
        id=new_uuid7(),
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        cycle_id=cycle.id,
        suggestion_id=row.id,
        decision="accepted",
        paragraph_indexes=indexes,
        acknowledged_stale=bool(stale),
        decided_by=guards.actor_id(access),
    )
    db.add(decision_row)
    db.flush()

    saved = sections.save_working(
        db,
        access,
        cycle_id,
        section_key,
        IcaapSectionWorkingSave(base_rev=payload.base_rev, doc=merged),
    )
    decision_row.inserted_doc_sha256 = sha256_hex(canonical_json(merged))
    decision_row.resulting_working_rev = saved.working_rev
    record_event(
        db,
        access.ctx,
        event_type="icaap.ai_draft.accepted",
        entity_type="icaap_ai_suggestion",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "section_key": section_key,
            "paragraph_indexes": indexes,
            "acknowledged_stale": bool(stale),
            "working_rev": saved.working_rev,
        },
    )
    observability.log_ai_event(
        observability.EVENT_DECIDED,
        feature=FEATURE,
        suggestion_id=str(row.id),
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        cycle_id=str(cycle.id),
        section_key=section_key,
        decision="accepted",
    )
    db.commit()
    return sections.get_section(db, access, cycle_id, section_key)


def reject(  # noqa: PLR0913 - the addressed draft is five path parts plus a body
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    section_key: str,
    suggestion_id: UUID,
    payload: IcaapAiDraftReject,
) -> IcaapAiSuggestionRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    row = _suggestion_or_404(db, access, cycle, section_key, suggestion_id)
    if _decision_for(db, access, row) is not None:
        raise guards.conflict("ai_draft_already_decided", "This draft has already been dealt with.")
    db.add(
        IcaapAiSuggestionDecision(
            id=new_uuid7(),
            organization_id=access.ctx.organization_id,
            bank_id=access.bank.id,
            cycle_id=cycle.id,
            suggestion_id=row.id,
            decision="rejected",
            paragraph_indexes=[],
            reason=payload.reason,
            decided_by=guards.actor_id(access),
        )
    )
    record_event(
        db,
        access.ctx,
        event_type="icaap.ai_draft.rejected",
        entity_type="icaap_ai_suggestion",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "section_key": section_key,
            "reason": payload.reason,
        },
    )
    observability.log_ai_event(
        observability.EVENT_DECIDED,
        feature=FEATURE,
        suggestion_id=str(row.id),
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        cycle_id=str(cycle.id),
        section_key=section_key,
        decision="rejected",
    )
    db.commit()
    db.refresh(row)
    return read_one(db, access, cycle, row)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def is_stale(db: Session, access: IcaapAccess, cycle: IcaapCycle, row: IcaapAiSuggestion) -> bool:
    """True when a figure this draft quoted has been rebound since it was written."""
    recorded = row.fact_bindings or {}
    if not recorded:
        return False
    current = {
        str(block_id): binding.seq
        for block_id, binding in blocks_service.current_bindings(db, access, cycle).items()
    }
    return any(
        current.get(str(entry.get("block_id"))) != entry.get("binding_seq")
        for entry in recorded.values()
        if isinstance(entry, dict)
    )


def _bindings(row: IcaapAiSuggestion) -> dict[str, ai_convert.FactBinding]:
    return {
        fid: ai_convert.FactBinding(
            block_id=str(entry["block_id"]),
            fact_key=str(entry["fact_key"]),
            binding_seq=int(entry["binding_seq"]),
        )
        for fid, entry in (row.fact_bindings or {}).items()
        if isinstance(entry, dict)
    }


def _entity_values(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, row: IcaapAiSuggestion
) -> dict[str, str]:
    """Resolve entity keys server-side, from the CURRENT registers."""
    from app.services.ai import pseudonymise  # noqa: PLC0415 - shared foundation

    entity_map = pseudonymise.build_entity_map(
        db,
        access.bank,
        as_of_label=fmt.format_date(cycle.as_of_date),
        fiscal_year_label=str(cycle.as_of_date.year),
        framework_label=f"{cycle.framework_code} {cycle.framework_version}",
        basis_label=fmt.basis_label(str(cycle.basis)),
    )
    offered = set(row.entity_keys or [])
    return {key: value for key, value in entity_map.values.items() if key in offered}


def _fact_displays(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    bindings: Mapping[str, ai_convert.FactBinding],
) -> dict[str, tuple[str, str]]:
    """The REAL figure behind each placeholder, plus its block's status.

    The reviewer sees the platform's number before deciding, not the model's
    idea of one — there is no stage at which an unverified figure is displayed.
    """
    states = {state.block_id: state for state in blocks_service.block_states(db, access, cycle)}
    current = {
        str(block_id): binding
        for block_id, binding in blocks_service.current_bindings(db, access, cycle).items()
    }
    # The bank's own reporting currency, so an amount whose binding did not
    # record one still previews the way it will read in the report. Resolved
    # from the institution, never a literal (jurisdiction neutrality).
    currency = jurisdictions.base_currency(access.bank)
    out: dict[str, tuple[str, str]] = {}
    for fid, binding in bindings.items():
        live: IcaapBlockBinding | None = current.get(binding.block_id)
        fact = (live.facts or {}).get(binding.fact_key) if live is not None else None
        state = states.get(binding.block_id)
        out[fid] = (
            fmt.format_fact(fact, fallback_currency=currency),
            str(state.status) if state is not None else "unbound",
        )
    return out


def _requirement_labels(cycle: IcaapCycle) -> dict[str, str]:
    framework = guards.require_framework(cycle)
    labels: dict[str, str] = {}
    for section in framework.sections:
        for item in section.requirements:
            labels[item.id] = item.text
    return labels


def _section_def_or_404(framework: Any, section_key: str) -> SectionDef:
    if not framework.has_section(section_key):
        guards.not_found()
    return framework.section(section_key)


def _suggestion_or_404(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    section_key: str,
    suggestion_id: UUID,
) -> IcaapAiSuggestion:
    row = db.scalar(
        select(IcaapAiSuggestion).where(
            IcaapAiSuggestion.id == suggestion_id,
            IcaapAiSuggestion.organization_id == access.ctx.organization_id,
            IcaapAiSuggestion.cycle_id == cycle.id,
            IcaapAiSuggestion.section_key == section_key,
        )
    )
    if row is None:
        guards.not_found()
    return row


def _decision_for(
    db: Session, access: IcaapAccess, row: IcaapAiSuggestion
) -> IcaapAiSuggestionDecision | None:
    return db.scalar(
        select(IcaapAiSuggestionDecision).where(
            IcaapAiSuggestionDecision.organization_id == access.ctx.organization_id,
            IcaapAiSuggestionDecision.suggestion_id == row.id,
        )
    )


def _in_flight(
    db: Session, access: IcaapAccess, section_id: UUID, actor: UUID
) -> IcaapAiSuggestion | None:
    return db.scalar(
        select(IcaapAiSuggestion)
        .where(
            IcaapAiSuggestion.organization_id == access.ctx.organization_id,
            IcaapAiSuggestion.section_id == section_id,
            IcaapAiSuggestion.requested_by == actor,
            IcaapAiSuggestion.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(IcaapAiSuggestion.created_at.desc())
        .limit(1)
    )


def _display_name(db: Session, user_id: UUID | None) -> str | None:
    if user_id is None:
        return None
    return db.scalar(select(User.display_name).where(User.id == user_id))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def block_titles() -> Mapping[str, str]:
    return {block_type: spec.title for block_type, spec in BLOCK_CATALOGUE.items()}


def paragraph_indexes(rows: Sequence[Any]) -> list[int]:  # pragma: no cover - typing helper
    return [int(value) for value in rows]


__all__ = [
    "FEATURE",
    "accept",
    "enqueue",
    "get_draft",
    "is_stale",
    "list_drafts",
    "read_one",
    "reject",
]
