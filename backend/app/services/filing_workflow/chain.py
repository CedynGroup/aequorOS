"""A return's review chain in motion: send for approval, decide, send back, transmit.

This is the BoG package plane running the engine P3 built for the ICAAP
(``domain/workflow/chain.py``, ``services/filing_workflow/engine.py``). Four
properties are what the module exists for.

**A decision names the state it was taken on.** Every forward decision carries
the review digest (``domain/filing/review_digest.py``). A decision on a digest
that is no longer current is refused, so "the Approver signed off this return"
can never mean "the Approver signed off an earlier version of it".

**A decision is not taken by the person who prepared it, nor by whoever already
checked it this round.** The Preparer is a maker; every officer who has decided
an earlier stage of this round is a checker. Neither may decide the stage in
front of them — which is §3.3 layer 3: whoever prepared this round cannot
approve it, whoever approved cannot validate it. The condition is supplied to
the authorization evaluator so the refusal lands in the binding trace, and the
service re-checks it UNDER THE ROW LOCK, because two requests can race between
the two.

**A send-back names its target.** Returning to stage *k* bumps the round and
requires every stage from *k* onwards to decide again; stages before *k* keep
their decisions. The send-back is INTERNAL and is never confused with the
regulator's own outcomes: ORASS ``rejected`` and ``declined`` remain package
statuses.

**Transmission is the chain's last position, not a status.**
:func:`assert_transmission_permitted` is called from
``regulatory_reporting.workflow.transition`` — the single writer of
``-> submitted`` — rather than from a route, because the two gates this codebase
lost (D-069, and the withdrawn-evidence check missing from
``generate_frozen_package``) were both lost at a seam where one mint site had
the gate and a second did not.

``pending_approval`` and ``approved`` survive as PROJECTIONS of the chain's
position (:func:`project_status`), not as the source of truth. Existing sealed,
submitted and acknowledged packages have no chain and are never re-projected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import ConditionCheck, ConditionKind, Permission
from app.domain.filing import review_digest as digest_domain
from app.domain.filing import workflow as domain
from app.models import (
    FilingWorkflowTemplate,
    PackageStageDecision,
    PackageWorkflowStage,
    RegulatoryPackage,
)
from app.schemas.filing_workflow import (
    PackageChainRead,
    PackageChainViewerRead,
    PackageStageDecisionCreate,
    PackageStageDecisionRead,
    PackageStageRead,
)
from app.services.audit import record_event
from app.services.filing_workflow import engine, templates
from app.services.filing_workflow.errors import conflict, not_found

#: Statuses at which a package is finished with, one way or another. A chain is
#: never pinned onto one and a projection never rewrites one: what was filed was
#: filed, and what was superseded stays superseded.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"submitted", "acknowledged", "rejected", "declined", "superseded"}
)

#: The package plane's sentences for the shared blocker ladder. The ladder's
#: ORDER is the engine's and is the same on both planes.
_BLOCKER_MESSAGES = engine.BlockerMessages(
    signed_in="This action requires a signed-in user.",
    no_such_stage="This filing chain has no such stage.",
    not_open="This return is not with a reviewer.",
    not_current_stage="This is not the stage the return is waiting on.",
    is_maker="You prepared this return, so a different officer must review it.",
    is_checker=(
        "You have already decided an earlier stage of this review, so a different "
        "officer must take this one."
    ),
)


@dataclass(frozen=True)
class ChainState:
    """Everything a decision needs to know, read once under one lock."""

    package: RegulatoryPackage
    stages: tuple[PackageWorkflowStage, ...]
    decisions: tuple[PackageStageDecision, ...]
    facts: tuple[domain.DecisionFact, ...]
    review_digest: str
    makers: frozenset[str]
    source: str

    def stage(self, seq: int) -> PackageWorkflowStage | None:
        return next((stage for stage in self.stages if stage.seq == seq), None)

    @property
    def transmit_seq(self) -> int | None:
        return next((stage.seq for stage in self.stages if stage.transmit_on_approve), None)

    @property
    def complete(self) -> bool:
        """Has every reviewing stage approved in the round that still stands?"""
        reviewing = [
            stage.seq for stage in self.stages if stage.decision_kind in {"review", "approve"}
        ]
        if not reviewing:
            return False
        return all(
            domain.stage_decided(self.facts, seq, current_round=self.package.workflow_round)
            for seq in reviewing
        )


# ---------------------------------------------------------------------------
# Reading the chain
# ---------------------------------------------------------------------------


def _stage_rows(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> tuple[PackageWorkflowStage, ...]:
    return tuple(
        db.scalars(
            select(PackageWorkflowStage)
            .where(
                PackageWorkflowStage.organization_id == ctx.organization_id,
                PackageWorkflowStage.package_id == package.id,
            )
            .order_by(PackageWorkflowStage.seq.asc())
        )
    )


def _decision_rows(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> tuple[PackageStageDecision, ...]:
    return tuple(
        db.scalars(
            select(PackageStageDecision)
            .where(
                PackageStageDecision.organization_id == ctx.organization_id,
                PackageStageDecision.package_id == package.id,
            )
            .order_by(PackageStageDecision.created_at.asc())
        )
    )


def _fact(row: PackageStageDecision) -> domain.DecisionFact:
    return domain.DecisionFact(
        stage_seq=row.stage_seq,
        round=row.round,
        decision=row.decision,
        return_to_seq=row.return_to_seq,
        review_digest=row.review_digest,
        decided_by=str(row.decided_by),
    )


def review_digest(package: RegulatoryPackage) -> str:
    """What a decision taken right now would attest to."""
    return digest_domain.compute(
        return_code=package.return_code,
        reporting_date=package.reporting_date,
        basis=package.basis,
        version=package.version,
        content_digest=package.content_digest,
        snapshot_sha256=package.snapshot_sha256,
        register_state_digest=package.register_state_digest,
        checks_passed=package.checks_passed,
        validation_report=package.validation_report,
    )


def load_state(db: Session, ctx: TenantContext, package: RegulatoryPackage) -> ChainState:
    decisions = _decision_rows(db, ctx, package)
    facts = tuple(_fact(row) for row in decisions)
    stages = _stage_rows(db, ctx, package)
    return ChainState(
        package=package,
        stages=stages,
        decisions=decisions,
        facts=facts,
        review_digest=review_digest(package),
        # The officer who generated the return WROTE it. That is the whole of
        # the package plane's authorship: a return's figures come from the
        # engine, and the person who minted the package is the one who put them
        # in front of a reviewer.
        makers=domain.makers(facts, content_authors={str(package.generated_by)}),
        source=stages[0].source if stages else "platform_default",
    )


def _lock(db: Session, package: RegulatoryPackage) -> None:
    """Take the package's row lock without discarding pending work.

    ``Session.refresh`` re-reads the row and OVERWRITES the instance, so a
    caller that is mid-act — the certification ceremony, which has already set
    the attestation state on this object — would silently lose it. Flushing
    first puts that work in the transaction, and the re-read then returns it.
    """
    db.flush()
    db.refresh(package, with_for_update=True)


def _decision_blocker(state: ChainState, seq: int, actor: str | None) -> str | None:
    """Why this caller may not take the decision at ``seq`` — in plain language."""
    stage = state.stage(seq)
    return engine.decision_blocker(
        actor=actor,
        stage_exists=stage is not None,
        chain_open=state.package.status not in TERMINAL_STATUSES and bool(state.stages),
        is_current_stage=state.package.current_stage_seq == seq,
        makers=state.makers,
        checkers=domain.checkers(
            state.facts, current_round=state.package.workflow_round, exclude_seq=seq
        ),
        messages=_BLOCKER_MESSAGES,
        after_stage=(
            (
                stage is not None and stage.decision_kind == "prepare",
                "The preparation stage is left by sending the return for approval, "
                "not by a decision here.",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Pinning the chain
# ---------------------------------------------------------------------------


def materialise_stages(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> tuple[PackageWorkflowStage, ...]:
    """Copy the chain in force into the package. Idempotent: pinned once, never re-pinned."""
    existing = _stage_rows(db, ctx, package)
    if existing:
        return existing
    stages, source, template = templates.effective_stages(db, ctx, package.bank_id)
    _add_stage_rows(db, package, stages, source, template)
    return _stage_rows(db, ctx, package)


def _add_stage_rows(
    db: Session,
    package: RegulatoryPackage,
    stages: tuple[domain.FilingStage, ...],
    source: str,
    template: FilingWorkflowTemplate | None,
) -> None:
    for stage in stages:
        db.add(
            PackageWorkflowStage(
                organization_id=package.organization_id,
                bank_id=package.bank_id,
                package_id=package.id,
                seq=stage.seq,
                stage_key=stage.stage_key,
                title=stage.title,
                decision_kind=stage.decision_kind,
                officer_titles=list(stage.officer_titles),
                transmit_on_approve=stage.transmit_on_approve,
                source=source,
                template_id=None if template is None else template.id,
                template_version=None if template is None else template.version,
            )
        )
    db.flush()


# ---------------------------------------------------------------------------
# The status projection
# ---------------------------------------------------------------------------


def project_status(state: ChainState) -> str:
    """The lifecycle status the chain's position implies.

    ``pending_approval`` and ``approved`` are projections of where the chain is,
    not the source of truth (redesign §4). A package with no pinned chain keeps
    the status it has: that is every terminal package, and every package still
    in preparation.
    """
    package = state.package
    if not state.stages or package.status in TERMINAL_STATUSES:
        return package.status
    if state.complete:
        return "approved"
    if package.current_stage_seq is None or package.current_stage_seq <= 1:
        # Back with the Preparer. "generated" is the status a package in
        # preparation has carried since the first version of the hub.
        return "generated"
    return "pending_approval"


def apply_projection(db: Session, ctx: TenantContext, state: ChainState) -> None:
    """Move the package's status to match the chain, through the allowed table."""
    from app.services.regulatory_reporting.workflow import transition  # noqa: PLC0415 - cycle

    projected = project_status(state)
    if projected == state.package.status:
        return
    transition(db, ctx, state.package, projected, details={"source": "filing_chain"})


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


def stage_for_decision(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> PackageWorkflowStage | None:
    """The stage a decision request would land on: the one the return waits at."""
    if package.current_stage_seq is None:
        return None
    return db.scalar(
        select(PackageWorkflowStage).where(
            PackageWorkflowStage.organization_id == ctx.organization_id,
            PackageWorkflowStage.package_id == package.id,
            PackageWorkflowStage.seq == package.current_stage_seq,
        )
    )


def stage_permission(stage: PackageWorkflowStage | None) -> Permission:
    """SUBMIT for the transmitting stage, REVIEW for a review, APPROVE otherwise.

    This is what makes the three roles three AUTHORITIES rather than three
    labels. The ``validator`` bundle carries ``view`` + ``submit`` and
    deliberately not ``approve``; the approver bundle carries ``approve`` and
    not ``submit``. So an Approver cannot take the Validator's stage and a
    Validator cannot take the Approver's, without the stage engine needing to
    know a single role string.

    An unresolvable stage defaults to SUBMIT — the narrowest authority, held by
    the fewest officers — so a bad path token can never widen authority.
    """
    if stage is None:
        return Permission.SUBMIT
    if stage.transmit_on_approve:
        return Permission.SUBMIT
    if stage.decision_kind == "review":
        return Permission.REVIEW
    return Permission.APPROVE


def stage_authority(
    db: Session, ctx: TenantContext, package: RegulatoryPackage | None
) -> tuple[ConditionCheck, ...]:
    """The maker-checker condition for a stage decision, resolved for the evaluator.

    Returns no condition when the package or its chain cannot be resolved: the
    route then answers 404/409 from the service rather than turning a missing
    object into an authorization refusal.
    """
    if package is None or package.current_stage_seq is None:
        return ()
    state = load_state(db, ctx, package)
    if not state.stages:
        return ()
    blocker = _decision_blocker(
        state,
        package.current_stage_seq,
        str(ctx.actor_user_id) if ctx.actor_user_id else None,
    )
    separated = not engine.is_separation_blocker(blocker)
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=separated,
            reason=(
                "reviewer neither prepared nor already decided this return"
                if separated
                else blocker or ""
            ),
        ),
    )


# ---------------------------------------------------------------------------
# The choke point
# ---------------------------------------------------------------------------


def assert_transmission_permitted(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> None:
    """Refuse a move to ``submitted`` unless the chain says it may be filed.

    Called from ``regulatory_reporting.workflow.transition`` — the one writer of
    ``-> submitted`` — so every channel, the manual record and the downtime
    re-upload pass it, and a future filing path gets it without remembering to.

    A package with NO pinned chain is not admitted by silence: it is admitted
    only when it is already ``submitted`` (the guarded ORASS re-upload of a
    downtime email filing, whose chain completed before the first submission).
    Everything else must have gone through a chain.
    """
    if package.status == "submitted":
        # The BG/FMD/2026/07 re-upload: the same filing, completing. Its chain
        # was already satisfied when it first reached the regulator.
        return
    state = load_state(db, ctx, package)
    if not state.stages:
        raise conflict(
            "filing_chain_not_started",
            "This return has not been through its review chain. Send it for approval "
            "first — nothing reaches the regulator without a reviewed chain behind it.",
        )
    if not state.complete:
        stage = state.stage(state.package.current_stage_seq or 0)
        raise conflict(
            "filing_chain_incomplete",
            (
                f"This return is still with {stage.title}."
                if stage is not None
                else "This return has not finished its review chain."
            )
            + " Every stage of the chain approves before it is transmitted.",
            current_stage_seq=state.package.current_stage_seq,
            round=state.package.workflow_round,
        )
    transmit = state.transmit_seq
    if transmit is None:  # pragma: no cover - validate_stages forbids it
        raise conflict("filing_chain_invalid", "This filing chain names no transmitting stage.")


# ---------------------------------------------------------------------------
# Recording decisions
# ---------------------------------------------------------------------------


def record_decision(  # noqa: PLR0913 - a decision is exactly these named parts
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    stage: PackageWorkflowStage,
    *,
    decision: str,
    review_digest_value: str,
    return_to_seq: int | None = None,
    comment: str | None = None,
    actor: UUID | None = None,
    authority: dict[str, Any] | None = None,
) -> PackageStageDecision:
    decided_by = actor or ctx.actor_user_id
    if decided_by is None:  # pragma: no cover - refused upstream
        raise conflict("actor_required", "This action requires a signed-in user.")
    name, title = engine.signer_display(db, ctx, decided_by)
    row = PackageStageDecision(
        organization_id=package.organization_id,
        bank_id=package.bank_id,
        package_id=package.id,
        stage_seq=stage.seq,
        stage_key=stage.stage_key,
        round=package.workflow_round,
        decision=decision,
        return_to_seq=return_to_seq,
        review_digest=review_digest_value,
        comment=comment,
        decided_by=decided_by,
        decided_by_name=name,
        officer_title=title,
        authority=authority or {},
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        ctx,
        event_type=f"regulatory_package.stage.{decision}",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "return_code": package.return_code,
            "reporting_date": package.reporting_date.isoformat(),
            "version": package.version,
            "stage_seq": stage.seq,
            "stage_key": stage.stage_key,
            "round": package.workflow_round,
            "decision": decision,
            "return_to_seq": return_to_seq,
            "review_digest": review_digest_value,
            "comment": comment,
        },
    )
    return row


def start_chain(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    note: str | None = None,
    actor: UUID | None = None,
) -> ChainState:
    """The Preparer's act: pin the chain and hand the return to the next stage.

    Machine validation GATES ENTRY here and nowhere else — it is not a stage and
    not a person, so ``checks_passed`` is read rather than the ``validated``
    status that used to stand for it.
    """
    _lock(db, package)
    if not package.checks_passed:
        raise conflict(
            "checks_not_passed",
            "The checks on this return have not passed. Clear them and re-run the "
            "checks before sending it for approval.",
            status=package.status,
        )
    report = package.validation_report or {}
    if report.get("error_count", 0) or not report.get("passed"):
        raise conflict(
            "checks_not_passed",
            "The latest check results carry ERROR findings; resolve them and re-run "
            "the checks before sending this return for approval.",
        )
    stages = materialise_stages(db, ctx, package)
    first = stages[0]
    state = load_state(db, ctx, package)
    if package.current_stage_seq is not None and package.current_stage_seq > 1:
        raise conflict(
            "filing_chain_in_review",
            "This return is already with a reviewer.",
            current_stage_seq=package.current_stage_seq,
        )
    record_decision(
        db,
        ctx,
        package,
        first,
        decision="submitted",
        review_digest_value=state.review_digest,
        comment=note,
        actor=actor,
    )
    package.current_stage_seq = min(2, len(stages))
    refreshed = load_state(db, ctx, package)
    apply_projection(db, ctx, refreshed)
    return load_state(db, ctx, package)


def decide(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    payload: PackageStageDecisionCreate,
) -> ChainState:
    """One stage's decision, taken under the package's row lock.

    The lock is the point: the maker-checker answer the authorization evaluator
    gave was computed when the request arrived, and two officers can decide
    between then and the write. Re-checking here is what makes "whoever approved
    cannot validate" a rule rather than a race. The lock is taken HERE rather
    than left to the caller, because a caller that forgot it would leave a rule
    that looks enforced and is not.
    """
    _lock(db, package)
    state = load_state(db, ctx, package)
    if not state.stages:
        raise conflict(
            "filing_chain_not_started",
            "This return has not been sent for approval.",
            status=package.status,
        )
    seq = package.current_stage_seq
    stage = None if seq is None else state.stage(seq)
    if stage is None:
        raise conflict(
            "filing_chain_not_started",
            "This return is not with a reviewer.",
            status=package.status,
        )
    if payload.round != package.workflow_round:
        raise conflict(
            "stage_round_moved",
            "This review has moved to a later round. Reload the chain.",
            round=package.workflow_round,
        )
    if payload.review_digest != state.review_digest:
        raise conflict(
            "review_basis_changed",
            "The return changed since this page was loaded. Reload it and decide again.",
            expected=state.review_digest,
        )
    blocker = _decision_blocker(state, stage.seq, str(ctx.actor_user_id or ""))
    if blocker is not None:
        raise conflict("maker_checker", blocker, stage_seq=stage.seq)
    engine.require_officer_title(
        stage_seq=stage.seq,
        stage_title=stage.title,
        officer_titles=stage.officer_titles,
        job_title=engine.signer_display(db, ctx, ctx.actor_user_id)[1]
        if ctx.actor_user_id
        else None,
        subject="this return",
    )

    if payload.decision == "returned":
        return _send_back(db, ctx, package, state, stage, payload)

    expected = domain.DECISION_FOR_KIND[stage.decision_kind]
    # The decision may already be on record: the transmitting stage takes TWO
    # steps that can fail independently — recording the approval, then handing
    # the return to the regulator — and a transmission that fails leaves the
    # first done and the second not. Re-deciding then collided with
    # ``uq_package_stage_decisions_forward`` and surfaced as a 500, with no way
    # forward for a return that was approved and unfiled.
    #
    # So the decision is idempotent and the TRANSMISSION is what retries.
    already = any(
        decision.stage_seq == stage.seq and decision.round == package.workflow_round
        for decision in state.decisions
    )
    if not already:
        record_decision(
            db,
            ctx,
            package,
            stage,
            decision=expected,
            review_digest_value=state.review_digest,
            comment=payload.comment,
        )
        # DELIBERATELY NO ADVANCE. Approving and handing on are two acts
        # (founder decision 2026-09-20): the officer approves, and then sends
        # the return to the next stage — or, at the transmitting stage, files
        # it. Advancing here collapsed them into one, which is why the screen
        # had no separate "Send to Validator" to offer.
        #
        # The cost is real and was argued: a return can now sit approved and
        # un-sent, on nobody's action list. ``hand_off`` below is the only way
        # forward from that state, and the queue keeps such a return visible
        # precisely so it cannot be lost.
    refreshed = load_state(db, ctx, package)
    apply_projection(db, ctx, refreshed)

    return load_state(db, ctx, package)


def awaiting_hand_off(state: ChainState) -> bool:
    """Is the current stage decided, with the return not yet passed on?

    The state between the two acts. It is derived rather than stored: a stage
    is awaiting hand-off when it carries a forward decision for the round the
    package is in, and the package still sits on it.
    """
    seq = state.package.current_stage_seq
    if seq is None:
        return False
    return any(
        decision.stage_seq == seq
        and decision.round == state.package.workflow_round
        and decision.decision != "returned"
        for decision in state.decisions
    )


def hand_off(db: Session, ctx: TenantContext, package: RegulatoryPackage) -> ChainState:
    """Pass an APPROVED return to the next stage — the second of the two acts.

    Refuses unless the current stage has already been decided this round, so
    the button that calls it can be offered from the start and simply be
    unavailable until the approval exists: the screen and the server agree on
    what "not yet" means instead of the screen guessing.
    """
    _lock(db, package)
    state = load_state(db, ctx, package)
    seq = package.current_stage_seq
    stage = None if seq is None else state.stage(seq)
    if stage is None:
        raise conflict(
            "filing_chain_not_started",
            "This return is not with a reviewer.",
            status=package.status,
        )
    if not awaiting_hand_off(state):
        raise conflict(
            "stage_not_decided",
            "Approve this return before sending it on.",
            stage_seq=stage.seq,
        )
    if stage.seq < len(state.stages):
        package.current_stage_seq = stage.seq + 1
    refreshed = load_state(db, ctx, package)
    apply_projection(db, ctx, refreshed)
    return load_state(db, ctx, package)


def _send_back(  # noqa: PLR0913 - the send-back is its six named parts
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    state: ChainState,
    stage: PackageWorkflowStage,
    payload: PackageStageDecisionCreate,
) -> ChainState:
    """A named target, a mandatory comment, and a round increment.

    A Validator may send back to the Approver or to the Preparer; an Approver
    may send back to the Preparer. The target is a stage, not a status going
    backwards, so "returned to the Preparer" is legible on the timeline rather
    than inferred — and it is never confused with the regulator's ``rejected``.
    """
    target = engine.require_send_back(
        target=payload.return_to_seq, stage_seq=stage.seq, comment=payload.comment
    )
    record_decision(
        db,
        ctx,
        package,
        stage,
        decision="returned",
        review_digest_value=state.review_digest,
        return_to_seq=target,
        comment=payload.comment,
    )
    package.workflow_round += 1
    package.current_stage_seq = target
    refreshed = load_state(db, ctx, package)
    apply_projection(db, ctx, refreshed)
    return load_state(db, ctx, package)


def record_stage_approval(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    actor: UUID,
    comment: str | None,
) -> PackageStageDecision | None:
    """The chain decision that a checker's CERTIFICATION is (no commit).

    An approver's signature over the frozen figures and their approval of the
    return are one act, so the chain decision is written in the same transaction
    as the signature rather than waiting for a second click. Returns ``None``
    when there is no chain to record against — a package certified before it was
    ever sent for approval keeps today's behaviour.

    What it does NOT do is hand the return on. Signing and approving are one
    act; approving and RELEASING are two (founder decision 2026-09-20), and
    that rule cannot depend on whether the tenant has attestation switched on —
    a bank that signs would otherwise get the very behaviour the bare approval
    path had removed, with the return arriving at the Validator on a signature
    nobody read as "send". ``hand_off`` is the only way forward from here for
    both paths.
    """
    state = load_state(db, ctx, package)
    seq = package.current_stage_seq
    stage = None if seq is None else state.stage(seq)
    if stage is None or stage.decision_kind == "prepare":
        return None
    if domain.stage_decided(state.facts, stage.seq, current_round=package.workflow_round):
        return None
    row = record_decision(
        db,
        ctx,
        package,
        stage,
        decision=domain.DECISION_FOR_KIND[stage.decision_kind],
        review_digest_value=state.review_digest,
        comment=comment,
        actor=actor,
    )
    return row


# ---------------------------------------------------------------------------
# The read contract
# ---------------------------------------------------------------------------


def _decision_read(row: PackageStageDecision, *, still_stands: bool) -> PackageStageDecisionRead:
    return PackageStageDecisionRead(
        id=row.id,
        stage_seq=row.stage_seq,
        stage_key=row.stage_key,
        round=row.round,
        decision=row.decision,  # pyright: ignore[reportArgumentType]
        return_to_seq=row.return_to_seq,
        review_digest=row.review_digest,
        comment=row.comment,
        decided_by=row.decided_by,
        decided_by_name=row.decided_by_name,
        officer_title=row.officer_title,
        created_at=row.created_at,
        still_stands=still_stands,
    )


def _stage_state(state: ChainState, stage: PackageWorkflowStage) -> str:
    package = state.package
    if package.current_stage_seq == stage.seq and package.status not in TERMINAL_STATUSES:
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


def read_chain(db: Session, ctx: TenantContext, package: RegulatoryPackage) -> PackageChainRead:
    """The chain as the dashboard reads it: stages, holder, decisions, round, viewer.

    When no chain is pinned yet, the stages a send-for-approval would pin are
    PREVIEWED, so the Preparer can see the three officers a return will pass
    through before it goes anywhere.
    """
    state = load_state(db, ctx, package)
    stages = state.stages
    source = state.source
    if not stages:
        pinned, source, _template = templates.effective_stages(db, ctx, package.bank_id)
        preview = [
            PackageStageRead(
                seq=stage.seq,
                stage_key=stage.stage_key,
                title=stage.title,
                decision_kind=stage.decision_kind,
                officer_titles=list(stage.officer_titles),
                transmit_on_approve=stage.transmit_on_approve,
                state="current" if stage.seq == 1 else "pending",
                decisions=[],
            )
            for stage in pinned
        ]
    else:
        by_stage: dict[int, list[PackageStageDecision]] = {}
        for row in state.decisions:
            by_stage.setdefault(row.stage_seq, []).append(row)
        preview = []
        for stage in stages:
            reset = domain.reset_round_for(state.facts, stage.seq)
            preview.append(
                PackageStageRead(
                    seq=stage.seq,
                    stage_key=stage.stage_key,
                    title=stage.title,
                    decision_kind=stage.decision_kind,  # pyright: ignore[reportArgumentType]
                    officer_titles=list(stage.officer_titles),
                    transmit_on_approve=stage.transmit_on_approve,
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
    actor = str(ctx.actor_user_id) if ctx.actor_user_id else None
    current = package.current_stage_seq
    blocker = None if current is None or not stages else _decision_blocker(state, current, actor)
    can_decide = bool(stages) and blocker is None and current is not None
    current_stage = None if current is None else state.stage(current)
    return PackageChainRead(
        package_id=package.id,
        status=package.status,  # pyright: ignore[reportArgumentType]
        round=package.workflow_round,
        current_stage_seq=current,
        current_stage_key=None if current_stage is None else current_stage.stage_key,
        source=source,  # pyright: ignore[reportArgumentType]
        review_digest=state.review_digest,
        checks_passed=package.checks_passed,
        complete=state.complete,
        awaiting_hand_off=awaiting_hand_off(state),
        is_rehearsal=package.is_rehearsal,
        stages=preview,
        viewer=PackageChainViewerRead(
            can_send_for_approval=(not stages or (current is not None and current <= 1))
            and package.status not in TERMINAL_STATUSES
            and package.checks_passed
            and actor is not None,
            can_decide=can_decide,
            can_return=can_decide and (current or 0) > 1,
            can_transmit=state.complete and not package.is_rehearsal,
            blocked_reason=blocker,
        ),
    )


def get_chain(
    db: Session, ctx: TenantContext, package: RegulatoryPackage | None
) -> PackageChainRead:
    if package is None:  # pragma: no cover - resolved by the dependency
        not_found()
    return read_chain(db, ctx, package)


__all__ = [
    "TERMINAL_STATUSES",
    "ChainState",
    "assert_transmission_permitted",
    "decide",
    "get_chain",
    "load_state",
    "materialise_stages",
    "project_status",
    "read_chain",
    "record_decision",
    "record_stage_approval",
    "review_digest",
    "stage_authority",
    "stage_for_decision",
    "stage_permission",
    "start_chain",
]
