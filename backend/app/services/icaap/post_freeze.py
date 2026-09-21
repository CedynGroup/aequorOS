"""Sending a FROZEN ICAAP back to a review stage.

A frozen report is sealed. Something still has to be able to reopen it — a
supervisor rejects the filing, the Board asks for a change before signing, a
figure turns out to be wrong — and the honest way to do that is a recorded
send-back, not a quiet edit of a sealed document.

Two mechanics make it safe.

**Two flushes, not one.** The cycle row is guarded: a sealed row admits only a
declared transition. Moving it to ``returned`` and clearing its package in one
UPDATE would present the guard with a row that is neither sealed nor properly
unsealed. So the status moves first, on its own, and only then — with the row
already unsealed — are the package link and the seal timestamps cleared.

**The old package is not deleted.** It is unlinked, and it stays readable as
what was frozen on that date. The next freeze mints version 2 and supersedes
it. A filing that was made and then corrected is two versions, not one version
with its history rewritten.

**Four eyes, because this is the MORE destructive send-back.** It was the less
guarded one until 2026-09-20 (independent audit F1): the in-review send-back
carries a round check, a digest check, maker-checker and an officer-title check
(``workflow._return_in_review``), while this one — which voids the CRO's, the
CEO's and the Board's signatures and invalidates every forward decision — had
none of them. One holder of an APPROVER bundle could take it alone, on a
``board_approved`` cycle, from a stale page.

The rule is the mirror of D-030. D-030 says the officer who REVIEWED a round may
not freeze it, because freezing makes them the preparer of record. This says the
officer who FROZE it may not be the one who unseals it: sealing a filing and
tearing up the signatures on it are the two halves of one act, and one person
may not hold both. It costs a preparer nothing they did not already owe — they
needed another officer's approval to freeze, and they need another officer's
decision to reopen.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.core.authorization import ConditionCheck, ConditionKind
from app.models import Bank, RegulatoryPackage
from app.models.icaap import IcaapCycle
from app.schemas.icaap import IcaapReturnCreate, IcaapStagesRead
from app.services.audit import record_event
from app.services.icaap import guards, workflow

#: Statuses a frozen cycle can be sent back FROM. ``submitted`` only when the
#: regulator rejected the filing — a return with the supervisor is not a
#: document the bank may quietly reopen.
RETURNABLE_STATUSES: frozenset[str] = frozenset({"frozen", "board_approved"})

#: Recorded by ``freeze_cycle`` at the freeze stage. Not a ``FORWARD_DECISION``,
#: so ``domain.checkers`` does not carry the freezer — this module names them.
_FROZEN = "frozen"


def _package(db: Session, cycle: IcaapCycle) -> RegulatoryPackage | None:
    return None if cycle.package_id is None else db.get(RegulatoryPackage, cycle.package_id)


def _freezers(state: workflow.ChainState) -> frozenset[str]:
    """Who sealed the report this send-back would unseal, in the current round."""
    return frozenset(
        fact.decided_by
        for fact in state.facts
        if fact.decision == _FROZEN and fact.round == state.cycle.round
    )


def _return_blocker(state: workflow.ChainState, actor: str | None) -> str | None:
    """Why this caller may not send the frozen report back — in plain language."""
    if actor is None:
        return "This action requires a signed-in user."
    if actor in _freezers(state):
        return (
            "You froze this ICAAP, so a different officer must send it back. Sending it "
            "back voids the signatures on the report you sealed."
        )
    return None


def return_authority(
    db: Session, ctx: TenantContext, bank: Bank, cycle_id: UUID | None
) -> tuple[ConditionCheck, ...]:
    """The maker-checker condition for a post-freeze send-back, for the evaluator.

    Resolved on the OBJECT, like every other ICAAP decision dependency, so the
    refusal lands in the binding trace with its reason instead of being a check
    buried in a service. :func:`return_cycle` re-checks it under the row lock,
    because two requests can race between the two.
    """
    if cycle_id is None:
        return ()
    access = IcaapAccess(ctx=ctx, bank=bank)
    cycle = db.get(IcaapCycle, cycle_id)
    if (
        cycle is None
        or cycle.organization_id != ctx.organization_id
        or cycle.bank_id != bank.id
    ):
        return ()
    state = workflow.load_state(db, access, cycle)
    blocker = _return_blocker(state, str(ctx.actor_user_id) if ctx.actor_user_id else None)
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=blocker is None,
            reason=(
                "the officer sending this ICAAP back did not freeze it"
                if blocker is None
                else blocker
            ),
        ),
    )


def _may_return(cycle: IcaapCycle, package: RegulatoryPackage | None) -> None:
    if cycle.status in RETURNABLE_STATUSES:
        return
    if cycle.status == "submitted" and package is not None and package.status == "rejected":
        return
    raise guards.conflict(
        "cycle_not_returnable",
        "Only a frozen ICAAP, or one the regulator has rejected, can be sent back to "
        "a review stage.",
        status=cycle.status,
        package_status=None if package is None else package.status,
    )


def return_cycle(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapReturnCreate
) -> IcaapStagesRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    package = _package(db, cycle)
    _may_return(cycle, package)
    state = workflow.load_state(db, access, cycle)
    # The same three checks the in-review send-back has carried all along, and
    # the same refusal codes, so the two send-backs behave alike (audit F1).
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
    blocker = _return_blocker(state, str(guards.actor_id(access)))
    if blocker is not None:
        raise guards.conflict("maker_checker", blocker)
    freeze_seq = state.freeze_seq
    if freeze_seq is None:  # pragma: no cover - the chain validator forbids it
        raise guards.conflict("stage_chain_invalid", "This review chain has no freeze stage.")
    if payload.return_to_seq > freeze_seq:
        raise guards.unprocessable(
            "return_target_invalid",
            "A frozen ICAAP goes back to a stage at or before the approval that made "
            "it ready to freeze.",
            max_seq=freeze_seq,
        )
    stage = state.stage(freeze_seq)
    if stage is None:  # pragma: no cover - resolved above
        raise guards.conflict("stage_chain_invalid", "This review chain has no freeze stage.")
    # The send-back is recorded AT the freeze stage, so it is that stage's
    # officer titles it must satisfy — unseal a Board Risk Committee approval
    # and the record must not misstate who did it. Empty titles (the default,
    # and Ghana's) make this a no-op, exactly as for a stage decision.
    workflow.require_stage_title(stage, workflow.signer_display(db, access)[1])

    old_package_id = cycle.package_id
    try:
        # FLUSH 1: leave the sealed statuses, and nothing else. The guard sees a
        # declared transition out of a sealed state rather than a row that has
        # been unsealed and rewritten in the same statement.
        cycle.status = "returned"
        db.flush()
        # FLUSH 2: the row is unsealed now, so the seal's own fields may go.
        cycle.package_id = None
        cycle.frozen_at = None
        cycle.board_approved_at = None
        cycle.submitted_at = None
        cycle.round += 1
        cycle.current_stage_seq = payload.return_to_seq
        if payload.return_to_seq > 1:
            cycle.status = "in_review"
        db.flush()
        workflow.record_decision(
            db,
            access,
            cycle,
            stage,
            decision="returned",
            review_digest=state.review_digest,
            return_to_seq=payload.return_to_seq,
            comment=payload.reason,
            package_id=old_package_id,
        )
        if package is not None and package.status not in {"rejected", "declined"}:
            _void_attestation(db, access, package, payload.reason)
        record_event(
            db,
            access.ctx,
            event_type="icaap.cycle.returned_after_freeze",
            entity_type="icaap_cycle",
            entity_id=cycle.id,
            details={
                "package_id": None if old_package_id is None else str(old_package_id),
                "return_to_seq": payload.return_to_seq,
                "round": cycle.round,
                "reason": payload.reason,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(cycle)
    return workflow.get_stages(db, access, cycle_id)


def _void_attestation(
    db: Session, access: IcaapAccess, package: RegulatoryPackage, reason: str
) -> None:
    """Retire the signatures on the unlinked package, in the same transaction.

    Signatures are never deleted — voiding starts a new attestation cycle and
    the old ones stay as the record of who signed what. A package the regulator
    has already rejected keeps its signatures untouched: they are part of the
    filing that was made.
    """
    from app.services.attestation import workflow as attestation  # noqa: PLC0415 - avoid a cycle

    if package.attestation_state == "unsigned" or package.status in {
        "submitted",
        "acknowledged",
    }:
        return
    attestation.void_attestation(
        db,
        access.ctx,
        package,
        reason=f"ICAAP sent back to a review stage: {reason}",
        commit=False,
    )


__all__ = ["RETURNABLE_STATUSES", "return_cycle"]
