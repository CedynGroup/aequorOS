"""Walk a return through its review chain, for suites whose subject is elsewhere.

Since the filing chain landed (``docs/filing_workflow_redesign.md`` §3, §6 steps
3-4), ``approved`` is a PROJECTION of a complete chain and the transmission gate
reads the chain rather than the status. Setting ``package.status = "approved"``
by hand no longer makes a return filable, and it should not: that is the gate
working.

Suites about notifications, the transition hook table or ORASS fidelity are not
about the chain, but they do need a filable return. They call this rather than
each growing its own three-officer fixture — and rather than being loosened to
pass, which would mean the control they never meant to test quietly stopped
being tested.

Nothing here bypasses a control. It records the same decisions, through the same
service, that three officers pressing three buttons would.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.filing import workflow as domain
from app.models import RegulatoryPackage
from app.services.filing_workflow import chain as filing_chain

#: Stable fixture officers, distinct from any package's ``generated_by`` so the
#: maker-checker rule is satisfied rather than sidestepped.
CHAIN_APPROVER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CHAIN_VALIDATOR = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


def complete_chain(
    db: Session,
    package: RegulatoryPackage,
    *,
    approver: UUID | None = None,
    validator: UUID | None = None,
) -> None:
    """Record every outstanding reviewing decision, so the return may be filed.

    Idempotent over stages that already decided in the current round, so it can
    follow a suite that has already driven the Preparer and the Approver.
    """
    ctx = TenantContext(
        organization_id=package.organization_id, actor_user_id=package.generated_by
    )
    package.checks_passed = True
    stages = filing_chain.materialise_stages(db, ctx, package)
    actors = [actor or uuid4() for actor in (approver, validator)]
    for index, stage in enumerate(stages):
        if stage.decision_kind not in {"review", "approve"}:
            continue
        state = filing_chain.load_state(db, ctx, package)
        if domain.stage_decided(
            state.facts, stage.seq, current_round=package.workflow_round
        ):
            continue
        filing_chain.record_decision(
            db,
            ctx,
            package,
            stage,
            decision=domain.DECISION_FOR_KIND[stage.decision_kind],
            review_digest_value=state.review_digest,
            actor=actors[index % len(actors)],
        )
    package.current_stage_seq = stages[-1].seq
    db.flush()
    filing_chain.apply_projection(db, ctx, filing_chain.load_state(db, ctx, package))
    db.flush()


__all__ = ["CHAIN_APPROVER", "CHAIN_VALIDATOR", "complete_chain"]
