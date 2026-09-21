"""The filing review chain on a real return: three roles, rounds, and the gate.

This suite is the executable form of ``docs/filing_workflow_redesign.md`` §3:
the Preparer prepares and cannot validate, the Approver reviews and may send
back, the Validator reviews, approves and is the only officer whose act releases
the return — and may send it back to either of the other two.

It drives the SERVICES, not the routes, so the rules it pins are the ones that
hold wherever a caller comes from. The route-level authority (an Approver's
binding cannot satisfy the Validator's stage, because that stage takes
``Permission.SUBMIT``) is pinned in ``tests/api/test_package_authorization.py``.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Permission
from app.models import (
    BankReportingPeriod,
    FilingWorkflowTemplate,
    RegulatoryPackage,
    User,
)
from app.schemas.filing_workflow import PackageStageDecisionCreate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import (
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryPackageCreate,
)
from app.services import regulatory_liquidity
from app.services.filing_workflow import chain as filing_chain
from app.services.regulatory_reporting import generation, validation, workflow
from tests.factories.attestation import relax_signing
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

PREPARER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
APPROVER = TenantContext(
    organization_id=DEMO_ORG_ID,
    actor_user_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
)
VALIDATOR = TenantContext(
    organization_id=DEMO_ORG_ID,
    actor_user_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
)
REPORTING_DATE = date(2026, 3, 31)
_LONG_ENOUGH = "The HQLA total does not agree with the general ledger."


def _officer(db: Session, ctx: TenantContext, email: str, name: str) -> None:
    if db.scalar(select(User.id).where(User.id == ctx.actor_user_id)) is None:
        db.add(
            User(
                id=ctx.actor_user_id,
                organization_id=DEMO_ORG_ID,
                email=email,
                display_name=name,
            )
        )
        db.commit()


def _seed(db: Session) -> None:
    materialize_canonical_test_book(db)
    # This suite is about the review chain, not the signing ceremony; approving
    # and signing are one act for a return that requires signatures.
    relax_signing(db, organization_id=DEMO_ORG_ID, return_code="LCR-NSFR")
    _officer(db, APPROVER, "chain.approver@example.test", "Chain Approver")
    _officer(db, VALIDATOR, "chain.validator@example.test", "Chain Validator")
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    run = regulatory_liquidity.create_liquidity_run(
        db,
        PREPARER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded"


def _row(db: Session, package_id: UUID) -> RegulatoryPackage:
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == package_id))
    assert row is not None
    return row


def _prepared(db: Session) -> RegulatoryPackage:
    """A return that has passed its checks and is with the Preparer."""
    package = generation.generate_package(
        db,
        PREPARER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=REPORTING_DATE),
    )
    validation.validate_package(db, PREPARER, SAMPLE_BANK_ID, package.id)
    return _row(db, package.id)


def _sent_for_approval(db: Session) -> RegulatoryPackage:
    package = _prepared(db)
    workflow.request_approval(
        db, PREPARER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    return _row(db, package.id)


def _decide(  # noqa: PLR0913 - a decision is exactly these named parts
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    decision: str = "approved",
    return_to_seq: int | None = None,
    comment: str | None = None,
    hand_off: bool = True,
) -> None:
    """Take the stage's decision and, by default, pass the return on.

    Approving and handing on are two acts (founder decision 2026-09-20). Most
    of these tests care that a decision carries the return forward, so the
    helper does both; the ones that exercise the state BETWEEN them pass
    ``hand_off=False``.
    """
    state = filing_chain.load_state(db, ctx, package)
    filing_chain.decide(
        db,
        ctx,
        package,
        PackageStageDecisionCreate(
            decision=decision,  # pyright: ignore[reportArgumentType]
            round=package.workflow_round,
            review_digest=state.review_digest,
            return_to_seq=return_to_seq,
            comment=comment,
        ),
    )
    if hand_off and decision != "returned":
        filing_chain.hand_off(db, ctx, package)
    db.commit()


# ---------------------------------------------------------------------------
# The three roles
# ---------------------------------------------------------------------------


def test_three_officers_carry_one_return_from_preparation_to_the_regulator(
    db_session: Session,
) -> None:
    _seed(db_session)
    package = _prepared(db_session)
    assert package.checks_passed is True

    workflow.request_approval(
        db_session, PREPARER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    package = _row(db_session, package.id)
    chain_read = filing_chain.read_chain(db_session, PREPARER, package)
    assert [stage.title for stage in chain_read.stages] == ["Preparer", "Approver", "Validator"]
    assert chain_read.current_stage_key == "approval"
    assert package.status == "pending_approval"

    # The Approver's approval is NOT authority to file, and it is not the
    # hand-off either: approving records the decision and the return STAYS with
    # the approver until they send it on (founder decision 2026-09-20).
    workflow.decide_approval(
        db_session,
        APPROVER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved"),
    )
    package = _row(db_session, package.id)
    assert package.current_stage_seq == 2
    assert filing_chain.read_chain(db_session, APPROVER, package).awaiting_hand_off is True

    filing_chain.hand_off(db_session, APPROVER, package)
    db_session.commit()
    package = _row(db_session, package.id)
    assert package.current_stage_seq == 3
    assert package.status == "pending_approval"
    assert filing_chain.read_chain(db_session, VALIDATOR, package).complete is False

    _decide(db_session, VALIDATOR, package)
    package = _row(db_session, package.id)
    assert package.status == "approved"
    assert filing_chain.read_chain(db_session, VALIDATOR, package).complete is True

    submitted = workflow.submit_package(
        db_session,
        VALIDATOR,
        SAMPLE_BANK_ID,
        package.id,
        channel="manual",
        external_ref="BOG-RCPT-0001",
    )
    assert submitted.status == "submitted"


def test_the_preparer_cannot_approve_their_own_return(db_session: Session) -> None:
    _seed(db_session)
    package = _sent_for_approval(db_session)
    with pytest.raises(HTTPException) as exc:
        _decide(db_session, PREPARER, package)
    assert exc.value.status_code == 409
    assert "prepared this return" in str(exc.value.detail)


def test_whoever_approved_this_round_cannot_also_validate_it(db_session: Session) -> None:
    """§3.3 layer 3, re-checked under the row lock at the moment of the act."""
    _seed(db_session)
    package = _sent_for_approval(db_session)
    _decide(db_session, APPROVER, package)
    package = _row(db_session, package.id)
    assert package.current_stage_seq == 3

    with pytest.raises(HTTPException) as exc:
        _decide(db_session, APPROVER, package)
    assert exc.value.status_code == 409
    assert "already decided an earlier stage" in str(exc.value.detail)


def test_the_stage_names_the_authority_not_a_role_string(db_session: Session) -> None:
    """The Validator's stage takes SUBMIT; the Approver's takes APPROVE.

    That is what makes the three roles three AUTHORITIES: the ``validator``
    bundle carries ``submit`` and not ``approve``, the approver bundle the
    reverse, so neither can take the other's stage.
    """
    _seed(db_session)
    package = _sent_for_approval(db_session)
    stage = filing_chain.stage_for_decision(db_session, PREPARER, package)
    assert stage is not None
    assert filing_chain.stage_permission(stage) is Permission.APPROVE

    _decide(db_session, APPROVER, package)
    package = _row(db_session, package.id)
    stage = filing_chain.stage_for_decision(db_session, PREPARER, package)
    assert stage is not None
    assert stage.transmit_on_approve is True
    assert filing_chain.stage_permission(stage) is Permission.SUBMIT
    # An unresolvable stage falls to the narrowest authority, never the widest.
    assert filing_chain.stage_permission(None) is Permission.SUBMIT


# ---------------------------------------------------------------------------
# Send-back: a named target, a mandatory comment, a round
# ---------------------------------------------------------------------------


def test_the_validator_sends_back_to_the_approver_and_the_round_moves(
    db_session: Session,
) -> None:
    _seed(db_session)
    package = _sent_for_approval(db_session)
    _decide(db_session, APPROVER, package)
    package = _row(db_session, package.id)

    _decide(
        db_session,
        VALIDATOR,
        package,
        decision="returned",
        return_to_seq=2,
        comment=_LONG_ENOUGH,
    )
    package = _row(db_session, package.id)
    assert package.current_stage_seq == 2
    assert package.workflow_round == 2
    # A return to the Approver does not reopen the text, so the Preparer is not
    # asked for anything and the return stays with a reviewer.
    assert package.status == "pending_approval"

    chain_read = filing_chain.read_chain(db_session, APPROVER, package)
    returned = [
        decision
        for stage in chain_read.stages
        for decision in stage.decisions
        if decision.decision == "returned"
    ]
    assert len(returned) == 1
    assert returned[0].return_to_seq == 2
    assert returned[0].comment == _LONG_ENOUGH
    assert returned[0].round == 1
    # The Approver's round-1 approval no longer stands: they must look again.
    approvals = [
        decision
        for stage in chain_read.stages
        for decision in stage.decisions
        if decision.stage_seq == 2 and decision.decision == "approved"
    ]
    assert approvals and approvals[0].still_stands is False
    assert chain_read.complete is False


def test_the_validator_can_send_back_to_the_preparer_instead(db_session: Session) -> None:
    _seed(db_session)
    package = _sent_for_approval(db_session)
    _decide(db_session, APPROVER, package)
    package = _row(db_session, package.id)

    _decide(
        db_session,
        VALIDATOR,
        package,
        decision="returned",
        return_to_seq=1,
        comment=_LONG_ENOUGH,
    )
    package = _row(db_session, package.id)
    assert package.current_stage_seq == 1
    assert package.workflow_round == 2
    # Back with the Preparer, which is what 'generated' has always meant.
    assert package.status == "generated"


def test_a_send_back_names_a_stage_and_says_why(db_session: Session) -> None:
    _seed(db_session)
    package = _sent_for_approval(db_session)

    with pytest.raises(HTTPException) as exc:
        _decide(db_session, APPROVER, package, decision="returned", comment=_LONG_ENOUGH)
    assert exc.value.status_code == 422
    assert "return_target_invalid" in str(exc.value.detail)

    with pytest.raises(HTTPException) as exc:
        _decide(db_session, APPROVER, package, decision="returned", return_to_seq=1, comment="no")
    assert exc.value.status_code == 422
    assert "return_comment_required" in str(exc.value.detail)


def test_a_decision_taken_on_a_stale_page_is_refused(db_session: Session) -> None:
    _seed(db_session)
    package = _sent_for_approval(db_session)
    with pytest.raises(HTTPException) as exc:
        filing_chain.decide(
            db_session,
            APPROVER,
            package,
            PackageStageDecisionCreate(
                decision="approved",
                round=package.workflow_round,
                review_digest="0" * 64,
            ),
        )
    assert exc.value.status_code == 409
    assert "review_basis_changed" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# Machine validation gates ENTRY; it is not a stage and not a person
# ---------------------------------------------------------------------------


def test_checks_passed_gates_entry_to_the_chain(db_session: Session) -> None:
    _seed(db_session)
    package = generation.generate_package(
        db_session,
        PREPARER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=REPORTING_DATE),
    )
    row = _row(db_session, package.id)
    assert row.checks_passed is False
    with pytest.raises(HTTPException) as exc:
        workflow.request_approval(
            db_session, PREPARER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
        )
    assert exc.value.status_code == 409
    assert "checks_not_passed" in str(exc.value.detail)


def test_re_running_the_checks_does_not_drag_a_return_out_of_the_chain(
    db_session: Session,
) -> None:
    """Checks are an attribute, not a step: re-running them is not a send-back."""
    _seed(db_session)
    package = _sent_for_approval(db_session)
    assert package.current_stage_seq == 2
    with pytest.raises(HTTPException) as exc:
        validation.validate_package(db_session, PREPARER, SAMPLE_BANK_ID, package.id)
    # A return with a reviewer is not in preparation, so there is nothing to
    # re-run — the refusal names the position rather than silently rewinding.
    assert exc.value.status_code == 409
    assert "still in preparation" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# The choke point
# ---------------------------------------------------------------------------


def test_no_path_to_the_regulator_skips_the_chain(db_session: Session) -> None:
    """The gate lives in ``transition``, the one writer of ``-> submitted``.

    Two filing gates have already been lost in this codebase at a seam where one
    mint site had the check and a second did not (D-069). A caller that reaches
    past the routes still meets this one.
    """
    _seed(db_session)
    package = _sent_for_approval(db_session)
    # Force the status past the chain the way a stray writer would, then try to
    # file. The transition table alone would allow 'approved' -> 'submitted'.
    package.status = "approved"
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        workflow.submit_package(
            db_session,
            VALIDATOR,
            SAMPLE_BANK_ID,
            package.id,
            channel="manual",
            external_ref="BOG-RCPT-0002",
        )
    assert exc.value.status_code == 409
    assert "filing_chain_incomplete" in str(exc.value.detail)


def test_a_return_that_never_entered_the_chain_cannot_be_filed(db_session: Session) -> None:
    _seed(db_session)
    package = _prepared(db_session)
    package.status = "approved"
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        workflow.submit_package(
            db_session,
            VALIDATOR,
            SAMPLE_BANK_ID,
            package.id,
            channel="manual",
            external_ref="BOG-RCPT-0003",
        )
    assert exc.value.status_code == 409
    assert "filing_chain_not_started" in str(exc.value.detail)


def test_a_rehearsal_stays_unfilable_through_the_chain(db_session: Session) -> None:
    """D-068: a rehearsal runs the lifecycle and never transmits.

    Two independent statements, because the refusal must survive the chain
    rather than be replaced by it. The chain read never offers transmission for
    a rehearsal, and the channel guard refuses one BEFORE the transition check —
    so a rehearsal whose chain is complete is still refused.
    """
    _seed(db_session)
    package = _sent_for_approval(db_session)
    _decide(db_session, APPROVER, package)
    package = _row(db_session, package.id)
    _decide(db_session, VALIDATOR, package)
    package = _row(db_session, package.id)
    assert package.status == "approved"
    assert filing_chain.read_chain(db_session, VALIDATOR, package).viewer.can_transmit is True

    # The same return, marked a rehearsal: complete, and still not filable.
    rehearsal = RegulatoryPackage(
        id=package.id,
        organization_id=package.organization_id,
        bank_id=package.bank_id,
        return_family="icaap",
        return_code=package.return_code,
        reporting_date=package.reporting_date,
        frequency=package.frequency,
        basis=package.basis,
        status="approved",
        version=package.version,
        is_rehearsal=True,
        generated_by=package.generated_by,
        workflow_round=package.workflow_round,
        current_stage_seq=package.current_stage_seq,
        checks_passed=True,
    )
    assert filing_chain.read_chain(db_session, VALIDATOR, rehearsal).complete is True
    assert filing_chain.read_chain(db_session, VALIDATOR, rehearsal).viewer.can_transmit is False

    with pytest.raises(HTTPException) as exc:
        workflow._ensure_channel_submittable(db_session, rehearsal, "orass_sandbox")
    assert exc.value.status_code == 409
    assert "rehearsal_channel_not_permitted" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# The chain is the bank's, not the platform's
# ---------------------------------------------------------------------------


def test_a_package_pins_the_chain_in_force_and_never_re_pins_it(db_session: Session) -> None:
    _seed(db_session)
    package = _sent_for_approval(db_session)
    assert filing_chain.read_chain(db_session, PREPARER, package).source == "platform_default"

    # A bank approves its own four-stage chain AFTER this return is in review.
    db_session.add(
        FilingWorkflowTemplate(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            version=1,
            status="approved",
            stages=[
                {"seq": 1, "stage_key": "preparation", "title": "Preparer",
                 "decision_kind": "prepare", "officer_titles": [],
                 "transmit_on_approve": False},
                {"seq": 2, "stage_key": "risk", "title": "Risk Review",
                 "decision_kind": "review", "officer_titles": [],
                 "transmit_on_approve": False},
                {"seq": 3, "stage_key": "approval", "title": "Approver",
                 "decision_kind": "approve", "officer_titles": [],
                 "transmit_on_approve": False},
                {"seq": 4, "stage_key": "validation", "title": "Validator",
                 "decision_kind": "approve", "officer_titles": [],
                 "transmit_on_approve": True},
            ],
            reason="the bank adds a risk review before approval",
            proposed_by=uuid4(),
            decided_by=uuid4(),
            decided_at=package.generated_at,
        )
    )
    db_session.commit()

    # The in-flight return keeps the governance it was sent under.
    still = filing_chain.read_chain(db_session, PREPARER, package)
    assert [stage.title for stage in still.stages] == ["Preparer", "Approver", "Validator"]

    # A NEW return pins the bank's chain.
    fresh = _prepared(db_session)
    workflow.request_approval(
        db_session, PREPARER, SAMPLE_BANK_ID, fresh.id, PackageApprovalRequestCreate()
    )
    fresh = _row(db_session, fresh.id)
    read = filing_chain.read_chain(db_session, PREPARER, fresh)
    assert read.source == "bank_template"
    assert [stage.title for stage in read.stages] == [
        "Preparer",
        "Risk Review",
        "Approver",
        "Validator",
    ]


def test_a_return_sent_back_to_the_preparer_can_go_round_again(db_session: Session) -> None:
    """Round 2 is legible as round 2, and nothing from round 1 still counts."""
    _seed(db_session)
    package = _sent_for_approval(db_session)
    _decide(
        db_session,
        APPROVER,
        package,
        decision="returned",
        return_to_seq=1,
        comment=_LONG_ENOUGH,
    )
    package = _row(db_session, package.id)
    assert package.status == "generated"
    assert package.workflow_round == 2

    # The Preparer sends it again without re-running the checks: entry is gated
    # by ``checks_passed``, not by a status.
    workflow.request_approval(
        db_session, PREPARER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    package = _row(db_session, package.id)
    assert package.status == "pending_approval"
    assert package.current_stage_seq == 2

    _decide(db_session, APPROVER, package)
    package = _row(db_session, package.id)
    _decide(db_session, VALIDATOR, package)
    package = _row(db_session, package.id)
    assert package.status == "approved"

    read = filing_chain.read_chain(db_session, VALIDATOR, package)
    rounds = sorted(
        {decision.round for stage in read.stages for decision in stage.decisions}
    )
    assert rounds == [1, 2]
    stale = [
        decision
        for stage in read.stages
        for decision in stage.decisions
        if decision.round == 1 and decision.decision in {"submitted", "approved"}
    ]
    assert stale and all(decision.still_stands is False for decision in stale)


def test_the_validator_may_send_a_completed_return_back_before_filing(
    db_session: Session,
) -> None:
    """The last backward edge: approved, then looked at again, then returned.

    The status follows the chain here, which is why ``approved`` has to be able
    to move backwards at all — it is a projection, not a seal.
    """
    _seed(db_session)
    package = _sent_for_approval(db_session)
    _decide(db_session, APPROVER, package)
    package = _row(db_session, package.id)
    _decide(db_session, VALIDATOR, package)
    package = _row(db_session, package.id)
    assert package.status == "approved"

    _decide(
        db_session,
        VALIDATOR,
        package,
        decision="returned",
        return_to_seq=2,
        comment=_LONG_ENOUGH,
    )
    package = _row(db_session, package.id)
    assert package.status == "pending_approval"
    assert package.current_stage_seq == 2
    assert filing_chain.read_chain(db_session, APPROVER, package).complete is False

    with pytest.raises(HTTPException) as exc:
        workflow.submit_package(
            db_session,
            VALIDATOR,
            SAMPLE_BANK_ID,
            package.id,
            channel="manual",
            external_ref="BOG-RCPT-0009",
        )
    assert exc.value.status_code == 409
