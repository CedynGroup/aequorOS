"""Review, freeze, governed reopen, and immutable historical ICAAP filings."""

from __future__ import annotations

from copy import deepcopy
from typing import Literal, cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from hypothesis import strategies as st
from hypothesis.stateful import initialize, invariant, precondition, rule
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from app.icaap.domain import prosemirror
from app.icaap.models.workspace import IcaapCycle, IcaapStageDecision
from app.icaap.schemas.workspace import (
    IcaapCycleArchive,
    IcaapFreezeCreate,
    IcaapReturnCreate,
    IcaapSectionCommit,
    IcaapSectionWorkingSave,
    IcaapStageDecisionCreate,
    IcaapSubmitForReview,
)
from app.icaap.service import cycles, freeze, post_freeze, sections, workflow
from app.models import Bank, RegulatoryPackage
from tests.icaap.service.p3_support import (
    AS_OF,
    access_for,
    annual_payload,
    answer_every_requirement,
    attach,
    govern_first_as_of,
    make_p2_ready,
    make_user,
    seal_capital_run,
    write_every_section,
)
from tests.icaap.testing.assertions import (
    assert_distinct_reviewers,
    assert_frozen_version_unchanged,
    assert_refused_cycle_unchanged,
)
from tests.icaap.testing.generators import (
    freeze_checkpoints,
    icaap_review_histories,
    report_narratives,
)
from tests.support.assertions import assert_workflow_state
from tests.support.factories.authorization import grant_institution_authority
from tests.support.generators import ReviewHistory
from tests.support.helpers import ORG_1, ORG_2, USER_2
from tests.support.stateful import DatabaseMachine, run_database_machine, success_events


class FreezeMachine(DatabaseMachine):
    def __init__(self, fixture: Session, actors: tuple[IcaapAccess, ...]) -> None:
        super().__init__(fixture)
        self.actors = actors
        self.maker = actors[0]

    def prepare(self) -> None:
        cycle = cycles.create_cycle(self.db, self.maker, annual_payload())
        self.cycle_id = cycle.id
        write_every_section(self.db, self.maker, cycle.id)
        answer_every_requirement(self.db, self.maker, cycle.id)
        make_p2_ready(self.db, self.maker, cycle.id)
        attach(self.db, self.maker, cycle.id, "senior_management_report", sha="a" * 64)
        self.status = "draft"
        self.stage: int | None = None
        self.round = 1
        self.approvals: dict[int, UUID] = {}
        self.package_id: UUID | None = None
        self.freezer: UUID | None = None
        self.frozen_contents: dict[UUID, tuple[object, ...]] = {}
        self.has_uncommitted_changes = False
        self.committed_text = prosemirror.plain_text(
            sections.get_section(self.db, self.maker, cycle.id, "executive_summary").working_doc
        )

    def evidence(self) -> tuple[object, ...]:
        self.db.expire_all()
        return (
            cycles.get_cycle(self.db, self.maker, self.cycle_id).model_dump(),
            workflow.get_stages(self.db, self.maker, self.cycle_id).model_dump(),
            sections.get_section(
                self.db, self.maker, self.cycle_id, "executive_summary"
            ).model_dump(),
            success_events(self.db, self.cycle_id, "icaap."),
        )

    def package_content(self, package_id: UUID) -> tuple[object, ...]:
        package = self.db.get(RegulatoryPackage, package_id)
        assert package is not None
        return deepcopy(package.snapshot), package.content_digest, package.snapshot_sha256

    @initialize(checkpoint=freeze_checkpoints(), history=icaap_review_histories())
    def reach_checkpoint(self, checkpoint: str, history: ReviewHistory | None = None) -> None:
        self.prepare()
        if checkpoint == "archived":
            self.archive()
            return
        if checkpoint == "draft":
            return
        self.submit(False)
        if checkpoint == "review":
            return
        self.review(1, 2, "reviewed", False, False, 1)
        if checkpoint == "approval":
            return
        if history is not None:
            for fact in history.decisions:
                if fact.decision != "returned":
                    continue
                assert fact.return_to_seq is not None
                self.review(2, 3, "returned", False, False, fact.return_to_seq)
                if fact.return_to_seq == 1:
                    self.submit(False)
                self.review(1, 2, "reviewed", False, False, 1)
        self.review(2, 3, "approved", False, False, 1)
        if checkpoint == "ready":
            return
        self.seal(0, False)

    @rule(stale=st.booleans())
    def submit(self, stale: bool) -> None:
        legal = (
            self.status in {"draft", "returned"}
            and not self.has_uncommitted_changes
            and not stale
        )
        before = self.evidence()
        stages = workflow.get_stages(self.db, self.maker, self.cycle_id)
        try:
            workflow.submit_for_review(
                self.db,
                self.maker,
                self.cycle_id,
                IcaapSubmitForReview(review_digest="0" * 64 if stale else stages.review_digest),
            )
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code == 409
            if self.status in {"draft", "returned"} and self.has_uncommitted_changes:
                detail = cast(object, exc.detail)
                assert isinstance(detail, dict)
                assert cast(dict[str, object], detail)["error_code"] == "uncommitted_changes"
            assert_refused_cycle_unchanged(self.evidence(), before)
        else:
            assert legal
            self.status, self.stage = "in_review", 2

    def can_advance(self) -> bool:
        return self.status in {"draft", "returned", "in_review"}

    @precondition(can_advance)
    @rule()
    def advance(self) -> None:
        if self.status in {"draft", "returned"}:
            self.submit(False)
        elif self.stage == 2:
            self.review(1, 2, "reviewed", False, False, 1)
        elif self.stage == 3:
            self.review(2, 3, "approved", False, False, 1)
        else:
            self.seal(0, False)

    def can_return(self) -> bool:
        return self.status == "frozen"

    @precondition(can_return)
    @rule(return_seq=st.integers(1, 3))
    def return_current_report(self, return_seq: int) -> None:
        actor = 2 if self.freezer == self.maker.ctx.actor_user_id else 0
        self.reopen(actor, return_seq, False, False)

    @rule(
        actor=st.integers(0, 3),
        stage=st.integers(2, 4),
        decision=st.sampled_from(("reviewed", "approved", "returned")),
        stale_digest=st.booleans(),
        stale_round=st.booleans(),
        return_seq=st.integers(1, 4),
    )
    def review(  # noqa: PLR0913 - generated parts of an addressed stage decision
        self,
        actor: int,
        stage: int,
        decision: Literal["reviewed", "approved", "returned"],
        stale_digest: bool,
        stale_round: bool,
        return_seq: int,
    ) -> None:
        access = self.actors[actor]
        officer = access.ctx.actor_user_id
        legal = (
            self.status == "in_review"
            and stage == self.stage
            and stage in {2, 3}
            and actor != 0
            and officer not in self.approvals.values()
            and (stage != 2 or actor == 1)  # The CRO review requires that officer title.
            and not stale_digest
            and not stale_round
            and (
                (decision == "returned" and return_seq < stage)
                or (decision == "reviewed" and stage == 2)
                or (decision == "approved" and stage == 3)
            )
        )
        before = self.evidence()
        stages = workflow.get_stages(self.db, self.maker, self.cycle_id)
        try:
            workflow.decide_stage(
                self.db,
                access,
                self.cycle_id,
                stage,
                IcaapStageDecisionCreate(
                    decision=decision,
                    round=self.round + int(stale_round),
                    review_digest="0" * 64 if stale_digest else stages.review_digest,
                    return_to_seq=return_seq if decision == "returned" else None,
                    comment="Return this assessment for a documented correction.",
                ),
            )
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code in {404, 409, 422}
            assert_refused_cycle_unchanged(self.evidence(), before)
        else:
            assert legal and officer is not None
            if decision == "returned":
                self.round += 1
                self.stage = return_seq
                self.status = "returned" if return_seq == 1 else "in_review"
                self.approvals = {
                    seq: who for seq, who in self.approvals.items() if seq < return_seq
                }
            else:
                self.approvals[stage] = officer
                self.stage = stage + 1

    @rule(actor=st.integers(0, 3), stale=st.booleans())
    def seal(self, actor: int, stale: bool) -> None:
        access = self.actors[actor]
        legal = (
            self.status == "in_review"
            and self.stage == 4
            and access.ctx.actor_user_id not in self.approvals.values()
            and not stale
        )
        before = self.evidence()
        stages = workflow.get_stages(self.db, self.maker, self.cycle_id)
        try:
            out = freeze.freeze_cycle(
                self.db,
                access,
                self.cycle_id,
                IcaapFreezeCreate(
                    review_digest="0" * 64 if stale else stages.review_digest,
                    reason="Seal the approved assessment for filing.",
                ),
            )
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code == 409
            assert_refused_cycle_unchanged(self.evidence(), before)
        else:
            assert legal
            self.status = "frozen"
            self.package_id = out.package.id
            self.freezer = access.ctx.actor_user_id
            self.frozen_contents[out.package.id] = self.package_content(out.package.id)
            assert out.package.version == len(self.frozen_contents)

    @rule(
        actor=st.integers(0, 3),
        return_seq=st.integers(1, 4),
        stale_digest=st.booleans(),
        stale_round=st.booleans(),
    )
    def reopen(self, actor: int, return_seq: int, stale_digest: bool, stale_round: bool) -> None:
        access = self.actors[actor]
        legal = (
            self.status == "frozen"
            and access.ctx.actor_user_id != self.freezer
            and return_seq <= 3
            and not stale_digest
            and not stale_round
        )
        before = self.evidence()
        stages = workflow.get_stages(self.db, self.maker, self.cycle_id)
        try:
            post_freeze.return_cycle(
                self.db,
                access,
                self.cycle_id,
                IcaapReturnCreate(
                    return_to_seq=return_seq,
                    round=self.round + int(stale_round),
                    review_digest="0" * 64 if stale_digest else stages.review_digest,
                    reason="The Board requested a documented correction to this assessment.",
                ),
            )
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code in {409, 422}
            assert_refused_cycle_unchanged(self.evidence(), before)
        else:
            assert legal
            self.round += 1
            self.stage = return_seq
            self.status = "returned" if return_seq == 1 else "in_review"
            self.package_id, self.freezer = None, None
            self.approvals = {seq: who for seq, who in self.approvals.items() if seq < return_seq}

    @rule(text=report_narratives())
    def edit(self, text: str) -> None:
        legal = self.status in {"draft", "returned"}
        before = self.evidence()
        current = sections.get_section(self.db, self.maker, self.cycle_id, "executive_summary")
        doc = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
        }
        try:
            sections.save_working(
                self.db,
                self.maker,
                self.cycle_id,
                "executive_summary",
                IcaapSectionWorkingSave(doc=doc, base_rev=current.working_rev),
            )
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code == 409
            assert_refused_cycle_unchanged(self.evidence(), before)
        else:
            assert legal
            saved = sections.get_section(self.db, self.maker, self.cycle_id, "executive_summary")
            assert prosemirror.plain_text(saved.working_doc) == text
            self.has_uncommitted_changes = True
            expected_error = (
                "section_empty"
                if not text.strip()
                else "no_changes"
                if text == self.committed_text
                else None
            )
            before_commit = self.evidence()
            try:
                sections.commit_version(
                    self.db,
                    self.maker,
                    self.cycle_id,
                    "executive_summary",
                    IcaapSectionCommit(base_rev=saved.working_rev, note="Document the correction."),
                )
            except HTTPException as exc:
                self.db.rollback()
                assert expected_error is not None
                assert exc.status_code == (422 if expected_error == "section_empty" else 409)
                detail = cast(object, exc.detail)
                assert isinstance(detail, dict)
                assert cast(dict[str, object], detail)["error_code"] == expected_error
                assert_refused_cycle_unchanged(self.evidence(), before_commit)
            else:
                assert expected_error is None
                self.committed_text = text
                self.has_uncommitted_changes = False

    @rule()
    def archive(self) -> None:
        legal = self.status in {"draft", "returned"} and self.package_id is None
        before = self.evidence()
        try:
            cycles.archive_cycle(
                self.db, self.maker, self.cycle_id, IcaapCycleArchive(reason="Retire this draft.")
            )
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code == 409
            assert_refused_cycle_unchanged(self.evidence(), before)
        else:
            assert legal
            self.status = "archived"

    @rule(other_tenant=st.booleans())
    def scoped_read_and_mutation(self, other_tenant: bool) -> None:
        bank_id = "BK-ICSTM002" if other_tenant else "BK-ICSTM001"
        bank = self.db.get(Bank, bank_id)
        assert bank is not None
        ctx = (
            TenantContext(organization_id=ORG_2, actor_user_id=USER_2, authorization_version=1)
            if other_tenant
            else self.maker.ctx
        )
        outsider = IcaapAccess(ctx=ctx, bank=bank)
        before = self.evidence()
        for action in (
            lambda: cycles.get_cycle(self.db, outsider, self.cycle_id),
            lambda: cycles.archive_cycle(
                self.db, outsider, self.cycle_id, IcaapCycleArchive(reason="Try another bank.")
            ),
        ):
            try:
                action()
            except HTTPException as exc:
                self.db.rollback()
                assert exc.status_code == 404
            else:
                raise AssertionError("Another tenant/bank accessed the ICAAP")
        assert_refused_cycle_unchanged(self.evidence(), before)

    @invariant()
    def cycle_and_frozen_versions_match_model(self) -> None:
        self.db.expire_all()
        cycle = cycles.get_cycle(self.db, self.maker, self.cycle_id)
        assert_workflow_state(
            (cycle.status, cycle.current_stage_seq, cycle.round, cycle.package_id),
            (self.status, self.stage, self.round, self.package_id),
            domain="ICAAP",
        )
        section = sections.get_section(self.db, self.maker, self.cycle_id, "executive_summary")
        assert_workflow_state(
            section.has_uncommitted_changes,
            self.has_uncommitted_changes,
            domain="ICAAP section revisions",
        )
        stages = workflow.get_stages(self.db, self.maker, self.cycle_id)
        standing = {
            stage.seq: decision.decided_by
            for stage in stages.stages
            for decision in stage.decisions
            if decision.decision in {"reviewed", "approved"} and decision.still_stands
        }
        assert standing == self.approvals
        assert_distinct_reviewers(standing, self.maker.ctx.actor_user_id)
        for package_id, original in self.frozen_contents.items():
            assert_frozen_version_unchanged(self.package_content(package_id), original)
        if self.package_id is not None:
            persisted_cycle = self.db.get(IcaapCycle, self.cycle_id)
            assert persisted_cycle is not None and persisted_cycle.frozen_at is not None
            package = self.db.get(RegulatoryPackage, self.package_id)
            assert package is not None and package.generated_by == self.freezer


def prepare_officers(db: Session) -> tuple[IcaapAccess, ...]:
    maker = access_for(db)
    govern_first_as_of(db, AS_OF)
    seal_capital_run(db, maker)
    actors = (
        maker,
        access_for(
            db,
            make_user(
                db, email="state-cro@example.test", name="CRO", job_title="Chief Risk Officer"
            ),
        ),
        access_for(db, make_user(db, email="state-brc@example.test", name="BRC")),
        access_for(db, make_user(db, email="state-freezer@example.test", name="Preparer")),
    )
    for access, bundle in zip(
        actors[1:], (RoleBundle.APPROVER, RoleBundle.APPROVER, RoleBundle.ANALYST), strict=True
    ):
        officer = access.ctx.actor_user_id
        assert officer is not None
        grant_institution_authority(
            db,
            organization_id=ORG_1,
            bank_id=maker.bank.id,
            user_id=officer,
            bundle=bundle,
            module=ModuleScope.CAPITAL,
            sensitivity=SensitivityScope.CONFIDENTIAL,
        )
    for bank_id, org in (("BK-ICSTM001", ORG_1), ("BK-ICSTM002", ORG_2)):
        db.add(
            Bank(
                id=bank_id,
                organization_id=org,
                name="State machine fixture",
                short_name="State fixture",
                currency=maker.bank.currency,
                jurisdiction_code=maker.bank.jurisdiction_code,
                license_type=maker.bank.license_type,
                institution_type=maker.bank.institution_type,
            )
        )
    db.commit()
    return actors


def test_freeze_state_machine(canonical_book: Session, extra_frameworks: None) -> None:
    actors = prepare_officers(canonical_book)
    run_database_machine(lambda: FreezeMachine(canonical_book, actors))


@pytest.mark.parametrize("return_seq", [1, 2, 3])
def test_frozen_version_survives_return_edit_and_refreeze(
    canonical_book: Session, extra_frameworks: None, return_seq: int
) -> None:
    actors = prepare_officers(canonical_book)
    machine = FreezeMachine(canonical_book, actors)
    try:
        machine.reach_checkpoint("frozen")
        first_package = machine.package_id
        assert first_package is not None
        machine.edit("A sealed report must refuse this edit.")
        machine.reopen(0, 1, False, False)  # The freezer cannot undo their own seal.
        assert machine.status == "frozen"
        machine.reopen(2, return_seq, False, False)
        stages = workflow.get_stages(machine.db, machine.maker, machine.cycle_id)
        returned = [
            decision
            for stage in stages.stages
            for decision in stage.decisions
            if decision.decision == "returned"
        ]
        assert len(returned) == 1
        assert (returned[0].round, returned[0].return_to_seq) == (1, return_seq)
        machine.edit("The Board requested this documented correction.")
        for _ in range(5 - return_seq):
            machine.advance()
            machine.cycle_and_frozen_versions_match_model()
        assert machine.status == "frozen"
        assert machine.package_id != first_package
        previous = machine.db.get(RegulatoryPackage, first_package)
        assert previous is not None and previous.status == "superseded"
        assert len(machine.frozen_contents) == 2
    finally:
        machine.teardown()


@pytest.mark.parametrize("return_seq", [1, 2])
def test_legacy_post_freeze_return_preserves_evidence_and_allows_refreeze(
    canonical_book: Session, extra_frameworks: None, return_seq: int
) -> None:
    actors = prepare_officers(canonical_book)
    machine = FreezeMachine(canonical_book, actors)
    try:
        machine.reach_checkpoint("frozen")
        first_package = machine.package_id
        assert first_package is not None
        stages = workflow.get_stages(machine.db, machine.maker, machine.cycle_id)
        freeze_stage = stages.stages[2]
        cycle = machine.db.get(IcaapCycle, machine.cycle_id)
        assert cycle is not None
        cycle.status = "returned"
        machine.db.flush()
        cycle.package_id = None
        cycle.frozen_at = cycle.board_approved_at = cycle.submitted_at = None
        cycle.round = 2
        cycle.current_stage_seq = return_seq
        if return_seq > 1:
            cycle.status = "in_review"
        machine.db.flush()
        returner = actors[2].ctx.actor_user_id
        assert returner is not None
        legacy_return = IcaapStageDecision(
            organization_id=cycle.organization_id,
            bank_id=cycle.bank_id,
            cycle_id=cycle.id,
            stage_seq=freeze_stage.seq,
            stage_key=freeze_stage.stage_key,
            round=2,
            decision="returned",
            return_to_seq=return_seq,
            review_digest=stages.review_digest,
            package_id=first_package,
            comment="The Board requested a correction before the upgrade.",
            decided_by=returner,
            decided_by_name="BRC",
        )
        machine.db.add(legacy_return)
        machine.db.commit()
        machine.db.expire_all()
        legacy_id = legacy_return.id
        legacy_evidence = next(
            decision
            for stage in workflow.get_stages(machine.db, machine.maker, machine.cycle_id).stages
            for decision in stage.decisions
            if decision.id == legacy_id
        ).model_dump()
        machine.round, machine.stage = 2, return_seq
        machine.status = "returned" if return_seq == 1 else "in_review"
        machine.package_id, machine.freezer = None, None
        machine.approvals = {seq: who for seq, who in machine.approvals.items() if seq < return_seq}
        machine.cycle_and_frozen_versions_match_model()
        for _ in range(5 - return_seq):
            machine.advance()
            machine.cycle_and_frozen_versions_match_model()
        assert machine.status == "frozen" and machine.package_id != first_package
        machine.reopen(2, 3, False, False)
        for _ in range(2):
            machine.advance()
            machine.cycle_and_frozen_versions_match_model()
        assert machine.status == "frozen" and len(machine.frozen_contents) == 3
        previous = machine.db.get(RegulatoryPackage, first_package)
        assert previous is not None and previous.status == "superseded"
        persisted_return = machine.db.get(IcaapStageDecision, legacy_id)
        assert persisted_return is not None and persisted_return.round == 2
        returned = [
            decision
            for stage in workflow.get_stages(machine.db, machine.maker, machine.cycle_id).stages
            for decision in stage.decisions
            if decision.id == legacy_id
        ]
        assert len(returned) == 1 and returned[0].round == 2
        assert returned[0].model_dump() == legacy_evidence
    finally:
        machine.teardown()


@pytest.mark.parametrize(
    ("texts", "new_versions"),
    [
        ((" ",), 0),
        (("The Board requested a documented correction to the assessment.",) * 2, 1),
    ],
)
@pytest.mark.parametrize("status", ["draft", "returned"])
def test_empty_and_repeated_edits_preserve_committed_history(
    canonical_book: Session,
    extra_frameworks: None,
    texts: tuple[str, ...],
    new_versions: int,
    status: str,
) -> None:
    """Minimized model gaps: draft → blank edit; draft → same paragraph twice.

    Both refusals are the public service contract, not production defects.
    The generated machine must model them without discarding these inputs.
    """
    actors = prepare_officers(canonical_book)
    machine = FreezeMachine(canonical_book, actors)
    try:
        machine.reach_checkpoint("draft")
        if status == "returned":
            machine.submit(False)
            machine.review(1, 2, "returned", False, False, 1)
        assert machine.status == status
        before = sections.get_section(
            machine.db, machine.maker, machine.cycle_id, "executive_summary"
        )
        assert before.committed_version_no is not None
        for text in texts:
            machine.edit(text)
            machine.cycle_and_frozen_versions_match_model()
        after = sections.get_section(
            machine.db, machine.maker, machine.cycle_id, "executive_summary"
        )
        assert after.committed_version_no == before.committed_version_no + new_versions
        assert after.has_uncommitted_changes and machine.has_uncommitted_changes
        before_submit = machine.evidence()
        machine.submit(False)
        machine.cycle_and_frozen_versions_match_model()
        assert machine.status == status and machine.has_uncommitted_changes
        assert_refused_cycle_unchanged(machine.evidence(), before_submit)
        machine.advance()
        machine.cycle_and_frozen_versions_match_model()
        assert machine.status == status and machine.has_uncommitted_changes
        assert_refused_cycle_unchanged(machine.evidence(), before_submit)
        machine.edit("The revised assessment includes the agreed management action.")
        machine.cycle_and_frozen_versions_match_model()
        recovered = sections.get_section(
            machine.db, machine.maker, machine.cycle_id, "executive_summary"
        )
        assert recovered.committed_version_no == before.committed_version_no + new_versions + 1
        assert not recovered.has_uncommitted_changes and not machine.has_uncommitted_changes
        machine.submit(False)
        machine.cycle_and_frozen_versions_match_model()
        assert (machine.status, machine.stage) == ("in_review", 2)
        machine.advance()
        machine.cycle_and_frozen_versions_match_model()
        assert (machine.status, machine.stage) == ("in_review", 3)
    finally:
        machine.teardown()
