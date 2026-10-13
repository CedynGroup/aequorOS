"""A real return, independent review model, retries and regulator outcomes.

Service callers here are already authorized; API bundle enforcement is covered
by test_package_authorization.py. The model checks the service's four-eyes and
scope guards without importing its transition table or decision helpers.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Literal
from uuid import UUID

import pytest
from fastapi import HTTPException
from hypothesis import strategies as st
from hypothesis.stateful import initialize, invariant, precondition, rule
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import RoleBundle
from app.models import Bank, BankReportingPeriod, RegulatoryPackage, User
from app.reporting.schemas.filing_workflow import PackageStageDecisionCreate
from app.reporting.schemas.packages import PackageApprovalRequestCreate, RegulatoryPackageCreate
from app.reporting.service import generation, packages, validation, workflow
from app.reporting.service.filing_workflow import chain
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import regulatory_liquidity
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.reporting.testing.assertions import (
    assert_distinct_reviewers,
    assert_filed_record_unchanged,
    assert_refused_filing_unchanged,
)
from tests.reporting.testing.generators import (
    OUTCOMES,
    filing_checkpoints,
    filing_review_histories,
    regulator_outcomes,
)
from tests.support.assertions import assert_workflow_state
from tests.support.factories.attestation import relax_signing
from tests.support.factories.authorization import grant_institution_authority
from tests.support.generators import ReviewHistory
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2
from tests.support.stateful import DatabaseMachine, run_database_machine, success_events

DATE = date(2026, 3, 31)
APPROVER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CHECKER = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
OFFICERS = (USER_1, APPROVER, CHECKER)
OTHER_BANK = "BK-STM00001"
OTHER_TENANT_BANK = "BK-STM00002"


def context(actor: UUID = USER_1, organization: str = ORG_1) -> TenantContext:
    return TenantContext(organization_id=organization, actor_user_id=actor, authorization_version=1)


def prepare_book(db: Session) -> None:
    materialize_canonical_test_book(db)
    relax_signing(db, organization_id=ORG_1, return_code="LCR-NSFR")
    for index, actor in enumerate(OFFICERS[1:]):
        db.add(
            User(
                id=actor,
                organization_id=ORG_1,
                email=f"state-{index}@example.test",
                display_name="State officer",
            )
        )
    source = db.get(Bank, SAMPLE_BANK_ID)
    assert source is not None
    for bank_id, org in ((OTHER_BANK, ORG_1), (OTHER_TENANT_BANK, ORG_2)):
        db.add(
            Bank(
                id=bank_id,
                organization_id=org,
                name="State machine fixture",
                short_name="State fixture",
                currency=source.currency,
                jurisdiction_code=source.jurisdiction_code,
                license_type=source.license_type,
                institution_type=source.institution_type,
            )
        )
    db.commit()
    grant_institution_authority(
        db,
        organization_id=ORG_2,
        bank_id=OTHER_TENANT_BANK,
        user_id=USER_2,
        bundle=RoleBundle.ANALYST,
    )
    db.commit()
    period = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID, BankReportingPeriod.period_end == DATE
        )
    )
    assert period is not None
    run = regulatory_liquidity.create_liquidity_run(
        db,
        context(),
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded"
    db.commit()


class FilingMachine(DatabaseMachine):
    def prepare(self) -> None:
        generated = generation.generate_package(
            self.db,
            context(),
            SAMPLE_BANK_ID,
            RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=DATE),
        )
        assert generated.version == 1
        self.package_id = generated.id
        validation.validate_package(self.db, context(), SAMPLE_BANK_ID, self.package_id)
        self.status = "validated"
        self.stage: int | None = None
        self.round = 1
        self.approvals: dict[int, UUID] = {}
        self.snapshot = deepcopy(self.row().snapshot)

    def row(self) -> RegulatoryPackage:
        self.db.expire_all()
        row = self.db.get(RegulatoryPackage, self.package_id)
        assert row is not None
        return row

    def evidence(self) -> tuple[object, ...]:
        row = self.row()
        return (
            row.status,
            row.current_stage_seq,
            row.workflow_round,
            deepcopy(row.snapshot),
            row.content_digest,
            chain.read_chain(self.db, context(), row).model_dump(),
            success_events(self.db, self.package_id, "regulatory_package."),
        )

    @initialize(checkpoint=filing_checkpoints(), history=filing_review_histories())
    def reach_checkpoint(self, checkpoint: str, history: ReviewHistory | None = None) -> None:
        # Reach important states through actual commands, never by assigning status.
        self.prepare()
        if checkpoint == "prepared":
            return
        self.send()
        if checkpoint == "review":
            return
        self.approve(1, False)
        if checkpoint == "decided":
            return
        self.hand_off()
        if checkpoint == "validation":
            return
        self.approve(2, False)
        if history is not None:
            for fact in history.decisions:
                if fact.decision != "returned":
                    continue
                assert fact.return_to_seq is not None
                self.send_back(2, False, fact.return_to_seq)
                for _ in range(4):  # Preparation, approval, hand-off, final approval.
                    if self.status == "approved":
                        break
                    self.advance("acknowledged")
                assert self.status == "approved", "Filing: valid return history failed to advance"
        if checkpoint == "approved":
            return
        self.submit()
        if checkpoint != "submitted":
            self.regulator(checkpoint)

    @rule()
    def send(self) -> None:
        legal = self.status in {"generated", "validated"} and self.stage in {None, 1}
        before = self.evidence()
        try:
            workflow.request_approval(
                self.db, context(), SAMPLE_BANK_ID, self.package_id, PackageApprovalRequestCreate()
            )
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code == 409
            assert_refused_filing_unchanged(self.evidence(), before)
        else:
            assert legal
            self.stage, self.status = 2, "pending_approval"

    def can_advance(self) -> bool:
        return self.status in {
            "generated",
            "validated",
            "pending_approval",
            "approved",
            "submitted",
        }

    @precondition(can_advance)
    @rule(outcome=regulator_outcomes())
    def advance(self, outcome: str) -> None:
        """Bias some steps toward progress without excluding adversarial rules."""
        if self.status in {"generated", "validated"}:
            self.send()
        elif self.status == "pending_approval":
            if self.stage in self.approvals:
                self.hand_off()
            else:
                actor = 2 if self.approvals.get(2) == APPROVER else 1
                self.approve(actor, False)
        elif self.status == "approved":
            self.submit()
        else:
            self.regulator(outcome)

    @rule()
    def revalidate(self) -> None:
        legal = self.status in {"generated", "validated"}
        before = self.evidence()
        try:
            out = validation.validate_package(self.db, context(), SAMPLE_BANK_ID, self.package_id)
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code == 409
            assert_refused_filing_unchanged(self.evidence(), before)
        else:
            assert legal and out.status == "validated"
            self.status = "validated"

    @rule(actor=st.integers(0, 2), stale=st.booleans())
    def approve(self, actor: int, stale: bool) -> None:
        self.decide("approved", actor, stale, 1)

    @rule(actor=st.integers(0, 2), stale=st.booleans(), return_seq=st.integers(1, 3))
    def send_back(self, actor: int, stale: bool, return_seq: int) -> None:
        self.decide("returned", actor, stale, return_seq)

    def decide(
        self, decision: Literal["approved", "returned"], actor: int, stale: bool, return_seq: int
    ) -> None:
        officer = OFFICERS[actor]
        legal = (
            self.status in {"pending_approval", "approved"}
            and self.stage in {2, 3}
            and officer != USER_1
            and all(officer != who for seq, who in self.approvals.items() if seq != self.stage)
            and not stale
            and (decision != "returned" or return_seq < (self.stage or 0))
        )
        before = self.evidence()
        row = self.row()
        digest = chain.read_chain(self.db, context(), row).review_digest
        try:
            chain.decide(
                self.db,
                context(officer),
                row,
                PackageStageDecisionCreate(
                    decision=decision,
                    round=self.round,
                    review_digest="0" * 64 if stale else digest,
                    return_to_seq=return_seq if decision == "returned" else None,
                    comment="Please reconcile the return with the ledger before approving.",
                ),
            )
            self.db.commit()
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code in {409, 422}
            assert_refused_filing_unchanged(self.evidence(), before)
        else:
            assert legal
            assert self.stage is not None
            if decision == "returned":
                self.round += 1
                self.stage = return_seq
                self.approvals = {
                    seq: who for seq, who in self.approvals.items() if seq < return_seq
                }
                self.status = "generated" if return_seq == 1 else "pending_approval"
            else:
                self.approvals.setdefault(self.stage, officer)
                if 2 in self.approvals and 3 in self.approvals:
                    self.status = "approved"

    @rule()
    def hand_off(self) -> None:
        # This service performs the chain hand-off only; channel transmission is
        # the separately exercised submission command below. A final-stage replay
        # is harmless: it must never alter the filed content or terminal status.
        legal = self.stage in self.approvals
        before = self.evidence()
        try:
            chain.hand_off(self.db, context(), self.row())
            self.db.commit()
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code == 409
            assert_refused_filing_unchanged(self.evidence(), before)
        else:
            assert legal
            if self.stage == 2:
                self.stage = 3

    @rule()
    def submit(self) -> None:
        legal = self.status == "approved"
        before = self.evidence()
        try:
            workflow.submit_package(
                self.db,
                context(CHECKER),
                SAMPLE_BANK_ID,
                self.package_id,
                channel="manual",
                external_ref="state-machine-receipt",
            )
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code == 409
            assert_refused_filing_unchanged(self.evidence(), before)
        else:
            assert legal
            self.status = "submitted"

    @rule(outcome=regulator_outcomes())
    def regulator(self, outcome: str) -> None:
        legal = self.status == "submitted"
        before = self.evidence()
        try:
            workflow.record_regulator_decision(
                self.db,
                context(CHECKER),
                SAMPLE_BANK_ID,
                self.package_id,
                channel="manual",
                event=outcome,
            )
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code == 409
            assert_refused_filing_unchanged(self.evidence(), before)
        else:
            assert legal
            self.status = outcome

    @rule(other_tenant=st.booleans())
    def scoped_read_and_mutation(self, other_tenant: bool) -> None:
        ctx = context(USER_2, ORG_2) if other_tenant else context()
        bank = OTHER_TENANT_BANK if other_tenant else OTHER_BANK
        before = self.evidence()
        for action in (
            lambda: packages.get_package(self.db, ctx, bank, self.package_id),
            lambda: workflow.request_approval(
                self.db, ctx, bank, self.package_id, PackageApprovalRequestCreate()
            ),
        ):
            try:
                action()
            except HTTPException as exc:
                self.db.rollback()
                assert exc.status_code == 404
            else:
                raise AssertionError("Another tenant/bank accessed the return")
        assert_refused_filing_unchanged(self.evidence(), before)

    @invariant()
    def return_matches_independent_model(self) -> None:
        row = self.row()
        assert_workflow_state(
            (row.status, row.current_stage_seq, row.workflow_round),
            (self.status, self.stage, self.round),
            domain="Filing",
        )
        assert_filed_record_unchanged(row.snapshot, self.snapshot)
        read = chain.read_chain(self.db, context(), row)
        standing = {
            stage.seq: decision.decided_by
            for stage in read.stages
            for decision in stage.decisions
            if decision.decision == "approved" and decision.still_stands
        }
        assert standing == self.approvals
        assert_distinct_reviewers(standing, USER_1)
        events = workflow.list_submission_events(
            self.db, context(), SAMPLE_BANK_ID, self.package_id
        )
        if self.status in {"submitted", *OUTCOMES}:
            assert any(event.event == "submitted" for event in events.events)
        if self.status in OUTCOMES:
            assert any(event.event == self.status for event in events.events)


def test_filing_state_machine(db_session: Session, return_generation_authority: None) -> None:
    prepare_book(db_session)
    run_database_machine(lambda: FilingMachine(db_session))


def test_repeated_manual_submission_cannot_add_a_second_receipt(
    db_session: Session, return_generation_authority: None
) -> None:
    """Hypothesis minimized this to reach submitted, then submit again."""
    prepare_book(db_session)
    machine = FilingMachine(db_session)
    try:
        machine.reach_checkpoint("submitted")
        before = machine.evidence()
        with pytest.raises(HTTPException) as caught:
            workflow.submit_package(
                machine.db,
                context(CHECKER),
                SAMPLE_BANK_ID,
                machine.package_id,
                channel="manual",
                external_ref="duplicate-receipt",
            )
        assert caught.value.status_code == 409
        assert machine.evidence() == before
        machine.return_matches_independent_model()
    finally:
        machine.teardown()
