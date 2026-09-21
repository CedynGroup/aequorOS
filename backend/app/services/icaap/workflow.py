"""The ICAAP review chain in motion: submit, review, approve, send back.

Three properties are what this module exists for, and each of them is the
answer to a way an approval record can be made worthless.

**A decision names the text it was taken on.** Every forward decision carries
the review digest (``domain/icaap/review_digest.py``). A decision on a digest
that is no longer current is refused, so "the CRO approved this ICAAP" can
never mean "the CRO approved an earlier draft of it".

**A decision is not taken by the person who wrote it.** Makers are the people
who committed a section, entered a table by hand, answered a checklist item or
submitted the report; checkers are the people who have already reviewed or
approved it in this round. Neither may decide the stage in front of them. The
condition is supplied to the authorization evaluator as a ``MAKER_CHECKER``
check so the refusal lands in the binding trace with its reason, and the
service re-checks it under the row lock because two requests can race between
the two.

**A send-back reopens exactly what it reopens.** Returning to stage *k* bumps
the round and requires every stage from *k* onwards to decide again; stages
before *k* keep their decisions, because nothing they looked at was reopened.
That rule lives in the pure domain and is read here, so the timeline, the
"can I decide?" answer and the freeze precondition cannot disagree.

Text and figures are LOCKED while a cycle is in review (``EDITABLE_STATUSES``
deliberately excludes ``in_review``). Evidence is not: see ``guards`` for why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.core.authorization import ConditionCheck, ConditionKind, Permission
from app.domain.icaap import review_digest as digest_domain
from app.domain.icaap import workflow as domain
from app.domain.icaap.frameworks.schema import Framework
from app.models import Bank
from app.models.icaap import (
    IcaapBlockBinding,
    IcaapCycle,
    IcaapCycleStage,
    IcaapDataBlock,
    IcaapSection,
    IcaapSectionVersion,
    IcaapStageDecision,
)
from app.schemas.icaap import (
    IcaapStageDecisionCreate,
    IcaapStageDecisionRead,
    IcaapStageRead,
    IcaapStagesRead,
    IcaapStageViewerRead,
    IcaapSubmitForReview,
)
from app.services.audit import record_event
from app.services.filing_workflow import engine
from app.services.icaap import guards, p2_state, workflow_templates


@dataclass(frozen=True)
class ChainState:
    """Everything a decision needs to know, read once under one lock."""

    cycle: IcaapCycle
    framework: Framework
    stages: tuple[IcaapCycleStage, ...]
    decisions: tuple[IcaapStageDecision, ...]
    facts: tuple[domain.DecisionFact, ...]
    review_digest: str
    makers: frozenset[str]

    @property
    def freeze_seq(self) -> int | None:
        return next((stage.seq for stage in self.stages if stage.freeze_on_approve), None)

    @property
    def attest_seq(self) -> int | None:
        return next((stage.seq for stage in self.stages if stage.decision_kind == "attest"), None)

    def stage(self, seq: int) -> IcaapCycleStage | None:
        return next((stage for stage in self.stages if stage.seq == seq), None)

    @property
    def awaiting_freeze(self) -> bool:
        """The freeze stage has approved and the cycle has not been frozen yet."""
        if self.cycle.status != "in_review" or self.freeze_seq is None:
            return False
        return self.cycle.current_stage_seq == self.attest_seq and any(
            fact.stage_seq == self.freeze_seq
            and fact.decision == "approved"
            and fact.round >= domain.reset_round_for(self.facts, self.freeze_seq)
            for fact in self.facts
        )


# ---------------------------------------------------------------------------
# Reading the chain
# ---------------------------------------------------------------------------


def _stage_rows(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> tuple[IcaapCycleStage, ...]:
    return tuple(
        db.scalars(
            select(IcaapCycleStage)
            .where(
                IcaapCycleStage.organization_id == access.ctx.organization_id,
                IcaapCycleStage.cycle_id == cycle.id,
            )
            .order_by(IcaapCycleStage.seq.asc())
        )
    )


def _decision_rows(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> tuple[IcaapStageDecision, ...]:
    return tuple(
        db.scalars(
            select(IcaapStageDecision)
            .where(
                IcaapStageDecision.organization_id == access.ctx.organization_id,
                IcaapStageDecision.cycle_id == cycle.id,
            )
            .order_by(IcaapStageDecision.created_at.asc())
        )
    )


def _fact(row: IcaapStageDecision) -> domain.DecisionFact:
    return domain.DecisionFact(
        stage_seq=row.stage_seq,
        round=row.round,
        decision=row.decision,
        return_to_seq=row.return_to_seq,
        review_digest=row.review_digest,
        decided_by=str(row.decided_by),
    )


def _sections(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> tuple[IcaapSection, ...]:
    return tuple(
        db.scalars(
            select(IcaapSection)
            .where(
                IcaapSection.organization_id == access.ctx.organization_id,
                IcaapSection.cycle_id == cycle.id,
            )
            .order_by(IcaapSection.position.asc())
        )
    )


def _latest_versions(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> dict[str, IcaapSectionVersion]:
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


def _live_blocks(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> tuple[tuple[IcaapDataBlock, IcaapBlockBinding | None], ...]:
    blocks = list(
        db.scalars(
            select(IcaapDataBlock)
            .where(
                IcaapDataBlock.organization_id == access.ctx.organization_id,
                IcaapDataBlock.cycle_id == cycle.id,
                IcaapDataBlock.retired_at.is_(None),
            )
            .order_by(IcaapDataBlock.block_key.asc())
        )
    )
    bindings: dict[UUID, IcaapBlockBinding] = {}
    for binding in db.scalars(
        select(IcaapBlockBinding)
        .where(
            IcaapBlockBinding.organization_id == access.ctx.organization_id,
            IcaapBlockBinding.cycle_id == cycle.id,
        )
        .order_by(IcaapBlockBinding.seq.asc())
    ):
        bindings[binding.block_id] = binding
    return tuple((block, bindings.get(block.id)) for block in blocks)


def current_review_digest(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> str:
    """What a decision taken right now would attest to."""
    framework, _matches = guards.framework_for(cycle)
    versions = _latest_versions(db, access, cycle)
    sections = _sections(db, access, cycle)
    requirements: list[digest_domain.RequirementDigestInput] = []
    for row in sections:
        for item_id, state in sorted((row.checklist_state or {}).items()):
            if not isinstance(state, dict):  # pragma: no cover - written only as a dict
                continue
            requirements.append(
                digest_domain.RequirementDigestInput(
                    section_key=row.section_key,
                    item_id=item_id,
                    status=str(state.get("status")),
                    reason=state.get("reason"),
                )
            )
    return digest_domain.compute(
        framework={
            "code": cycle.framework_code,
            "version": cycle.framework_version,
            "sha256": framework.digest,
        },
        sections=[
            digest_domain.SectionDigestInput(
                section_key=row.section_key,
                committed_version_no=row.committed_version_no,
                doc_sha256=(
                    versions[row.section_key].doc_sha256 if row.section_key in versions else None
                ),
            )
            for row in sections
        ],
        requirements=requirements,
        blocks=[
            digest_domain.BlockDigestInput(
                block_key=block.block_key,
                binding_seq=None if binding is None else binding.seq,
                payload_sha256=None if binding is None else binding.payload_sha256,
                pinned_binding_seq=block.pinned_binding_seq,
                pin_reason=block.pin_reason,
            )
            for block, binding in _live_blocks(db, access, cycle)
        ],
        p2_state_digest=p2_state.state_digest(db, access, cycle),
    )


def content_authors(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> frozenset[str]:
    """Everybody whose own work is in the report as it currently stands."""
    authors: set[str] = set()
    for version in _latest_versions(db, access, cycle).values():
        authors.add(str(version.committed_by))
    for _block, binding in _live_blocks(db, access, cycle):
        # Only a MANUAL binding is somebody's authorship. A figure the engine
        # produced was not written by whoever pressed refresh.
        if binding is not None and binding.source_kind == "manual":
            authors.add(str(binding.bound_by))
    for row in _sections(db, access, cycle):
        for state in (row.checklist_state or {}).values():
            if isinstance(state, dict) and state.get("updated_by"):
                authors.add(str(state["updated_by"]))
    return frozenset(authors)


def load_state(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> ChainState:
    framework, _matches = guards.framework_for(cycle)
    decisions = _decision_rows(db, access, cycle)
    facts = tuple(_fact(row) for row in decisions)
    return ChainState(
        cycle=cycle,
        framework=framework,
        stages=_stage_rows(db, access, cycle),
        decisions=decisions,
        facts=facts,
        review_digest=current_review_digest(db, access, cycle),
        makers=domain.makers(facts, content_authors=content_authors(db, access, cycle)),
    )


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


#: The ICAAP's own sentences for the shared blocker ladder
#: (``services/filing_workflow/engine.py``). The ladder's ORDER lives there and
#: is the same on both planes; only the wording is the ICAAP's.
_BLOCKER_MESSAGES = engine.BlockerMessages(
    signed_in="This action requires a signed-in user.",
    no_such_stage="This review chain has no such stage.",
    not_open="This ICAAP is not under review.",
    not_current_stage="This is not the stage the ICAAP is waiting on.",
    is_maker="You wrote part of this report, so a different officer must review it.",
    is_checker=(
        "You have already decided an earlier stage of this review, so a different "
        "officer must take this one."
    ),
)
_SEPARATION_MARKER = engine.SEPARATION_MARKER


def _decision_blocker(state: ChainState, seq: int, actor: str | None) -> str | None:
    """Why this caller may not take the decision at ``seq`` — in plain language."""
    stage = state.stage(seq)
    return engine.decision_blocker(
        actor=actor,
        stage_exists=stage is not None,
        chain_open=state.cycle.status == "in_review",
        is_current_stage=state.cycle.current_stage_seq == seq,
        makers=state.makers,
        checkers=domain.checkers(
            state.facts, current_round=state.cycle.round, exclude_seq=seq
        ),
        messages=_BLOCKER_MESSAGES,
        after_stage=(
            (
                stage is not None and stage.decision_kind == "attest",
                "The Board records its approval by signing the frozen report, not here.",
            ),
        ),
    )


def stage_authority(
    db: Session, ctx: TenantContext, bank: Bank, cycle_id: UUID | None, stage_seq: int | None
) -> tuple[ConditionCheck, ...]:
    """The maker-checker condition for a stage decision, resolved for the evaluator.

    Returns no condition when the cycle or stage cannot be resolved: the route
    then answers 404/409 from the service rather than turning a missing object
    into an authorization refusal.
    """
    if cycle_id is None or stage_seq is None:
        return ()
    cycle = db.scalar(
        select(IcaapCycle).where(
            IcaapCycle.id == cycle_id,
            IcaapCycle.organization_id == ctx.organization_id,
            IcaapCycle.bank_id == bank.id,
        )
    )
    if cycle is None:
        return ()
    access = IcaapAccess(ctx=ctx, bank=bank)
    state = load_state(db, access, cycle)
    blocker = _decision_blocker(
        state, stage_seq, str(ctx.actor_user_id) if ctx.actor_user_id else None
    )
    separated = not engine.is_separation_blocker(blocker)
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=separated,
            reason=(
                "reviewer neither wrote nor already decided this ICAAP"
                if separated
                else blocker or ""
            ),
        ),
    )


def stage_permission(
    db: Session, ctx: TenantContext, bank: Bank, cycle_id: UUID | None, stage_seq: int | None
) -> Permission:
    """REVIEW for a review stage, APPROVE for an approval stage.

    The distinction is the point of the ladder: an approver bundle carries both
    permissions, a reviewer bundle only the first, so a reviewer can record a
    review but cannot give the approval that makes the report ready to freeze.
    An unresolvable stage defaults to APPROVE — the stricter of the two — so a
    bad path token can never widen authority.
    """
    if cycle_id is None or stage_seq is None:
        return Permission.APPROVE
    stage = db.scalar(
        select(IcaapCycleStage).where(
            IcaapCycleStage.organization_id == ctx.organization_id,
            IcaapCycleStage.bank_id == bank.id,
            IcaapCycleStage.cycle_id == cycle_id,
            IcaapCycleStage.seq == stage_seq,
        )
    )
    if stage is not None and stage.decision_kind == "review":
        return Permission.REVIEW
    return Permission.APPROVE


def freeze_authority(
    db: Session, ctx: TenantContext, bank: Bank, cycle_id: UUID | None
) -> tuple[ConditionCheck, ...]:
    """Freeze is a MAKER's act: nobody who reviewed the round may take it (D-030).

    Freezing makes the actor the package's ``generated_by``, and a package
    generator may certify as preparer but never as approver or for the Board.
    If a reviewer froze, that reviewer would lose the signature slot they were
    appointed to fill — which is why freeze is an explicit act by the preparer
    rather than a side effect of the approval that precedes it.
    """
    if cycle_id is None:
        return ()
    cycle = db.scalar(
        select(IcaapCycle).where(
            IcaapCycle.id == cycle_id,
            IcaapCycle.organization_id == ctx.organization_id,
            IcaapCycle.bank_id == bank.id,
        )
    )
    if cycle is None:
        return ()
    access = IcaapAccess(ctx=ctx, bank=bank)
    state = load_state(db, access, cycle)
    actor = str(ctx.actor_user_id) if ctx.actor_user_id else None
    reviewers = domain.checkers(state.facts, current_round=state.cycle.round, exclude_seq=None)
    passed = actor is not None and actor not in reviewers
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=passed,
            reason=(
                "the person freezing this ICAAP did not review or approve it"
                if passed
                else (
                    "you reviewed or approved this ICAAP, so freezing it would make you "
                    "its preparer of record and cost you your signature slot"
                )
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Pinning the chain
# ---------------------------------------------------------------------------


def materialise_stages(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, framework: Framework
) -> tuple[IcaapCycleStage, ...]:
    """Copy the chain in force into the cycle. Idempotent: pinned once, never re-pinned."""
    existing = _stage_rows(db, access, cycle)
    if existing:
        return existing
    stages, source, template = workflow_templates.effective_stages(db, access, framework)
    for stage in stages:
        db.add(
            IcaapCycleStage(
                organization_id=cycle.organization_id,
                bank_id=cycle.bank_id,
                cycle_id=cycle.id,
                seq=stage.seq,
                stage_key=stage.stage_key,
                title=stage.title,
                decision_kind=stage.decision_kind,
                officer_titles=list(stage.officer_titles),
                freeze_on_approve=stage.freeze_on_approve,
                source=source,
                template_id=None if template is None else template.id,
                template_version=None if template is None else template.version,
            )
        )
    db.flush()
    return _stage_rows(db, access, cycle)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _decision_read(row: IcaapStageDecision, *, still_stands: bool) -> IcaapStageDecisionRead:
    return IcaapStageDecisionRead(
        id=row.id,
        stage_seq=row.stage_seq,
        stage_key=row.stage_key,
        round=row.round,
        decision=row.decision,
        return_to_seq=row.return_to_seq,
        review_digest=row.review_digest,
        package_id=row.package_id,
        comment=row.comment,
        decided_by=row.decided_by,
        decided_by_name=row.decided_by_name,
        officer_title=row.officer_title,
        created_at=row.created_at,
        still_stands=still_stands,
    )


def _stage_state(state: ChainState, stage: IcaapCycleStage) -> str:
    if state.cycle.current_stage_seq == stage.seq and state.cycle.status in {
        "in_review",
        "returned",
    }:
        return "current"
    reset = domain.reset_round_for(state.facts, stage.seq)
    decided = any(
        fact.stage_seq == stage.seq
        and fact.decision in domain.FORWARD_DECISIONS
        and fact.round >= reset
        for fact in state.facts
    )
    if decided:
        return "done"
    if any(fact.stage_seq == stage.seq and fact.decision == "returned" for fact in state.facts):
        return "returned"
    return "pending"


def get_stages(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapStagesRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework, _matches = guards.framework_for(cycle)
    state = load_state(db, access, cycle)
    stages = state.stages
    source = stages[0].source if stages else None
    if not stages:
        pinned, source, _template = workflow_templates.effective_stages(db, access, framework)
        preview = [
            IcaapStageRead(
                seq=stage.seq,
                stage_key=stage.stage_key,
                title=stage.title,
                decision_kind=stage.decision_kind,  # pyright: ignore[reportArgumentType]
                officer_titles=list(stage.officer_titles),
                freeze_on_approve=stage.freeze_on_approve,
                state="current" if stage.seq == 1 else "pending",
                decisions=[],
            )
            for stage in pinned
        ]
    else:
        by_stage: dict[int, list[IcaapStageDecision]] = {}
        for row in state.decisions:
            by_stage.setdefault(row.stage_seq, []).append(row)
        preview = []
        for stage in stages:
            reset = domain.reset_round_for(state.facts, stage.seq)
            preview.append(
                IcaapStageRead(
                    seq=stage.seq,
                    stage_key=stage.stage_key,
                    title=stage.title,
                    decision_kind=stage.decision_kind,  # pyright: ignore[reportArgumentType]
                    officer_titles=list(stage.officer_titles),
                    freeze_on_approve=stage.freeze_on_approve,
                    state=_stage_state(state, stage),  # pyright: ignore[reportArgumentType]
                    decisions=[
                        _decision_read(
                            row,
                            still_stands=(
                                row.decision not in domain.FORWARD_DECISIONS or row.round >= reset
                            ),
                        )
                        for row in by_stage.get(stage.seq, [])
                    ],
                )
            )
    actor = str(access.ctx.actor_user_id) if access.ctx.actor_user_id else None
    current = state.cycle.current_stage_seq
    blocker = None if current is None else _decision_blocker(state, current, actor)
    can_decide = blocker is None and current is not None
    reviewers = domain.checkers(state.facts, current_round=state.cycle.round, exclude_seq=None)
    return IcaapStagesRead(
        cycle_id=cycle.id,
        status=cycle.status,
        round=cycle.round,
        current_stage_seq=current,
        awaiting_freeze=state.awaiting_freeze,
        source=source or "framework_default",  # pyright: ignore[reportArgumentType]
        review_digest=state.review_digest,
        stages=preview,
        viewer=IcaapStageViewerRead(
            can_submit=cycle.status in guards.EDITABLE_STATUSES and not access.examiner,
            can_decide=can_decide and not access.examiner,
            can_return=can_decide and (current or 0) > 1 and not access.examiner,
            can_freeze=(
                state.awaiting_freeze
                and actor is not None
                and actor not in reviewers
                and not access.examiner
            ),
            blocked_reason=blocker,
        ),
    )


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def _uncommitted(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> list[str]:
    return [
        row.section_key
        for row in _sections(db, access, cycle)
        if row.committed_from_rev is None or row.committed_from_rev != row.working_rev
    ]


def signer_display(db: Session, access: IcaapAccess) -> tuple[str, str | None]:
    """The actor's name and job title AS THEY STAND NOW, copied into the record.

    A later rename must not rewrite who approved an ICAAP, so the decision row
    keeps the value rather than a foreign key to the live user.
    """
    return engine.signer_display(db, access.ctx, guards.actor_id(access))


def require_stage_title(stage: IcaapCycleStage, job_title: str | None) -> None:
    """The stage's officer titles, checked against the signer's recorded title.

    Empty ``officer_titles`` means role-only, which is the default: enforcing a
    guessed officer title would block a legitimate reviewer.
    """
    engine.require_officer_title(
        stage_seq=stage.seq,
        stage_title=stage.title,
        officer_titles=stage.officer_titles,
        job_title=job_title,
        subject="the ICAAP",
    )


def record_decision(  # noqa: PLR0913 - a decision is exactly these named parts
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    stage: IcaapCycleStage,
    *,
    decision: str,
    review_digest: str,
    return_to_seq: int | None = None,
    comment: str | None = None,
    package_id: UUID | None = None,
    authority: dict[str, Any] | None = None,
) -> IcaapStageDecision:
    name, title = signer_display(db, access)
    row = IcaapStageDecision(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        stage_seq=stage.seq,
        stage_key=stage.stage_key,
        round=cycle.round,
        decision=decision,
        return_to_seq=return_to_seq,
        review_digest=review_digest,
        package_id=package_id,
        comment=comment,
        decided_by=guards.actor_id(access),
        decided_by_name=name,
        officer_title=title,
        authority=authority or {},
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        access.ctx,
        event_type=f"icaap.stage.{decision}",
        entity_type="icaap_cycle",
        entity_id=cycle.id,
        details={
            "stage_seq": stage.seq,
            "stage_key": stage.stage_key,
            "round": cycle.round,
            "decision": decision,
            "return_to_seq": return_to_seq,
            "review_digest": review_digest,
            "comment": comment,
            "package_id": None if package_id is None else str(package_id),
        },
    )
    return row


def submit_for_review(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapSubmitForReview
) -> IcaapStagesRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    if cycle.status not in guards.EDITABLE_STATUSES:
        raise guards.conflict(
            "cycle_not_submittable",
            "Only a draft or returned ICAAP can be submitted for review.",
            status=cycle.status,
        )
    framework, digest_matches = guards.framework_for(cycle)
    if not digest_matches:
        raise guards.conflict(
            "framework_digest_mismatch",
            "The framework text has changed since this ICAAP was started. Move it onto "
            "the new version before submitting it for review.",
        )
    outstanding = _uncommitted(db, access, cycle)
    if outstanding:
        raise guards.conflict(
            "uncommitted_changes",
            "Some sections have edits that are not in a committed version. Commit or "
            "discard them — reviewers can only review committed text.",
            sections=sorted(outstanding),
        )
    stages = materialise_stages(db, access, cycle, framework)
    state = load_state(db, access, cycle)
    if payload.review_digest != state.review_digest:
        raise guards.conflict(
            "review_basis_changed",
            "The report changed since this page was loaded. Reload it and submit again.",
            expected=state.review_digest,
        )
    first = stages[0]
    # A resubmission goes back to the stage that sent it back, when the content
    # is the one that stage last saw; otherwise it restarts at stage 2, because
    # the later stages are being asked to look at something new.
    target = 2
    returns = [fact for fact in state.facts if fact.decision == "returned"]
    if returns:
        last = max(returns, key=lambda fact: fact.round)
        if last.review_digest == state.review_digest and last.return_to_seq == 1:
            target = 2
    if len(stages) < target:  # pragma: no cover - the chain validator forbids it
        raise guards.conflict("stage_chain_invalid", "This review chain has no reviewing stage.")
    record_decision(
        db,
        access,
        cycle,
        first,
        decision="submitted",
        review_digest=state.review_digest,
        comment=payload.note,
    )
    cycle.status = "in_review"
    cycle.current_stage_seq = target
    db.commit()
    db.refresh(cycle)
    return get_stages(db, access, cycle_id)


def decide_stage(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    stage_seq: int,
    payload: IcaapStageDecisionCreate,
) -> IcaapStagesRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    state = load_state(db, access, cycle)
    stage = state.stage(stage_seq)
    if stage is None:
        guards.not_found()
    if cycle.status != "in_review":
        raise guards.conflict(
            "cycle_not_in_review", "This ICAAP is not under review.", status=cycle.status
        )
    if cycle.current_stage_seq != stage_seq:
        raise guards.conflict(
            "stage_moved",
            "This ICAAP has moved on from that stage. Reload the review timeline.",
            current_stage_seq=cycle.current_stage_seq,
        )
    if stage.decision_kind == "attest":
        raise guards.conflict(
            "stage_decided_by_signature",
            "The Board records its approval by signing the frozen report, not by a decision here.",
        )
    if payload.round != cycle.round:
        raise guards.conflict(
            "stage_round_moved",
            "This review has moved to a later round. Reload the review timeline.",
            round=cycle.round,
        )
    if payload.review_digest != state.review_digest:
        raise guards.conflict(
            "review_basis_changed",
            "The report changed since this page was loaded. Reload it and decide again.",
            expected=state.review_digest,
        )
    submitted = [fact for fact in state.facts if fact.decision == "submitted"]
    if submitted:
        latest = max(submitted, key=lambda fact: fact.round)
        if latest.review_digest != state.review_digest:  # pragma: no cover - locked while in review
            raise guards.conflict(
                "review_basis_changed",
                "The report is not the one that was submitted for review.",
                submitted=latest.review_digest,
                current=state.review_digest,
            )
    blocker = _decision_blocker(state, stage_seq, str(guards.actor_id(access)))
    if blocker is not None:
        raise guards.conflict("maker_checker", blocker, stage_seq=stage_seq)
    require_stage_title(stage, signer_display(db, access)[1])

    if payload.decision == "returned":
        return _return_in_review(db, access, cycle, state, stage, payload)

    expected = domain.DECISION_FOR_KIND[stage.decision_kind]
    if payload.decision != expected:
        raise guards.unprocessable(
            "stage_decision_mismatch",
            f"{stage.title} records a decision of '{expected}'.",
            expected=expected,
        )
    record_decision(
        db,
        access,
        cycle,
        stage,
        decision=expected,
        review_digest=state.review_digest,
        comment=payload.comment,
    )
    cycle.current_stage_seq = stage_seq + 1
    db.commit()
    db.refresh(cycle)
    return get_stages(db, access, cycle_id)


def _return_in_review(  # noqa: PLR0913 - the return is its six named parts
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    state: ChainState,
    stage: IcaapCycleStage,
    payload: IcaapStageDecisionCreate,
) -> IcaapStagesRead:
    target = engine.require_send_back(
        target=payload.return_to_seq, stage_seq=stage.seq, comment=payload.comment
    )
    record_decision(
        db,
        access,
        cycle,
        stage,
        decision="returned",
        review_digest=state.review_digest,
        return_to_seq=target,
        comment=payload.comment,
    )
    cycle.round += 1
    cycle.current_stage_seq = target
    # Only a return to the preparation stage reopens the text. A return to a
    # later reviewer asks that reviewer to look again at the SAME document, so
    # the cycle stays in review and stays locked.
    cycle.status = "returned" if target == 1 else "in_review"
    db.commit()
    db.refresh(cycle)
    return get_stages(db, access, cycle.id)


__all__ = [
    "ChainState",
    "content_authors",
    "current_review_digest",
    "decide_stage",
    "freeze_authority",
    "get_stages",
    "load_state",
    "materialise_stages",
    "record_decision",
    "require_stage_title",
    "signer_display",
    "stage_authority",
    "stage_permission",
    "submit_for_review",
]
