"""Independent maker-checker model for filing and ICAAP review-chain versions.

Contexts represent already-authorized service callers. API grant enforcement and
cycle/package chain pinning have separate acceptance tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from hypothesis import strategies as st
from hypothesis.stateful import initialize, invariant, rule
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.icaap.domain.frameworks import registry
from app.icaap.schemas.workspace import (
    IcaapWorkflowTemplateCreate,
    IcaapWorkflowTemplateDecision,
    IcaapWorkflowTemplateListRead,
    IcaapWorkflowTemplateRead,
    IcaapWorkflowTemplateSubmit,
    IcaapWorkflowTemplateUpdate,
)
from app.icaap.service import workflow_templates as icaap
from app.models import Bank
from app.reporting.schemas.filing_workflow import (
    FilingWorkflowTemplateCreate,
    FilingWorkflowTemplateDecision,
    FilingWorkflowTemplateListRead,
    FilingWorkflowTemplateRead,
    FilingWorkflowTemplateSubmit,
    FilingWorkflowTemplateUpdate,
)
from app.reporting.service.filing_workflow import templates as filing
from tests.icaap.service.p3_support import FRAMEWORK_ROOT
from tests.icaap.testing.generators import icaap_stages
from tests.identity.api.test_grant_administration import BANK_A, BANK_B, GRANTEE
from tests.identity.api.test_grant_administration import (
    grant_client as grant_client,  # noqa: PLC0414 - shared pytest fixture
)
from tests.identity.testing.assertions import (
    assert_maker_checker,
    assert_refused_approval_unchanged,
    assert_sealed_approval_unchanged,
)
from tests.identity.testing.generators import template_checkpoints
from tests.reporting.testing.generators import filing_stages, stage_titles
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2
from tests.support.stateful import DatabaseMachine, run_database_machine, success_events

type TemplateRead = FilingWorkflowTemplateRead | IcaapWorkflowTemplateRead
type TemplateList = FilingWorkflowTemplateListRead | IcaapWorkflowTemplateListRead
type Kind = Literal["filing", "icaap"]
type Decision = Literal["approved", "rejected"]
ACTORS = (USER_1, GRANTEE)
REASON = "Review chain for the institution's governance."


@dataclass
class ExpectedTemplate:
    status: str
    maker: UUID
    version: int
    title: str
    sealed: str | None = None
    audit_events: int = 1


class TemplateMachine(DatabaseMachine):
    def __init__(self, fixture: Session, kind: Kind) -> None:
        super().__init__(fixture)
        self.kind = kind
        self.framework = registry.load_all((FRAMEWORK_ROOT,))[0]
        self.expected: dict[UUID, ExpectedTemplate] = {}
        self.current: UUID | None = None
        self.prefix = (
            "filing_workflow_template." if kind == "filing" else "icaap.workflow_template."
        )

    def scope(self, actor: int = 0, wrong: str = "none") -> IcaapAccess:
        bank = self.db.get(Bank, BANK_B if wrong == "bank" else BANK_A)
        assert bank is not None
        return IcaapAccess(
            ctx=TenantContext(
                organization_id=ORG_2 if wrong == "tenant" else ORG_1,
                actor_user_id=USER_2 if wrong == "tenant" else ACTORS[actor],
                authorization_version=1,
            ),
            bank=bank,
        )

    def listing(self, wrong: str = "none") -> TemplateList:
        access = self.scope(wrong=wrong)
        if self.kind == "filing":
            return filing.list_templates(self.db, access.ctx, access.bank)
        return icaap.list_templates(self.db, access, self.framework)

    def command(  # noqa: PLR0911, PLR0913 - two public service contracts, one model
        self,
        action: str,
        actor: int,
        template_id: UUID | None,
        title: str,
        decision: Decision = "approved",
        wrong: str = "none",
    ) -> TemplateRead:
        access = self.scope(actor, wrong)
        if action == "propose":
            if self.kind == "filing":
                return filing.propose_template(
                    self.db,
                    access.ctx,
                    access.bank,
                    FilingWorkflowTemplateCreate(stages=filing_stages(title), reason=REASON),
                )
            return icaap.propose_template(
                self.db,
                access,
                self.framework,
                IcaapWorkflowTemplateCreate(stages=icaap_stages(title), reason=REASON),
            )
        assert template_id is not None
        if self.kind == "filing":
            if action == "update":
                return filing.update_template(
                    self.db,
                    access.ctx,
                    access.bank,
                    template_id,
                    FilingWorkflowTemplateUpdate(stages=filing_stages(title), reason=REASON),
                )
            if action == "submit":
                return filing.submit_template(
                    self.db,
                    access.ctx,
                    access.bank,
                    template_id,
                    FilingWorkflowTemplateSubmit(reason=REASON),
                )
            return filing.decide_template(
                self.db,
                access.ctx,
                access.bank,
                template_id,
                FilingWorkflowTemplateDecision(decision=decision, reason=REASON),
            )
        if action == "update":
            return icaap.update_template(
                self.db,
                access,
                template_id,
                IcaapWorkflowTemplateUpdate(stages=icaap_stages(title), reason=REASON),
            )
        if action == "submit":
            return icaap.submit_template(
                self.db,
                access,
                template_id,
                IcaapWorkflowTemplateSubmit(reason=REASON),
            )
        return icaap.decide_template(
            self.db,
            access,
            template_id,
            IcaapWorkflowTemplateDecision(decision=decision, reason=REASON),
        )

    def evidence(self) -> tuple[object, ...]:
        self.db.expire_all()
        return (
            self.listing().model_dump_json(),
            tuple((key, success_events(self.db, key, self.prefix)) for key in self.expected),
        )

    def apply(  # noqa: PLR0913 - independently generated command dimensions
        self,
        action: str,
        actor: int = 0,
        index: int = -1,
        title: str = "Preparation",
        decision: Decision = "approved",
        wrong: str = "none",
    ) -> None:
        keys = list(self.expected)
        key = keys[index % len(keys)] if keys else None
        expected = self.expected[key] if key is not None else None
        if action == "propose":
            legal = all(
                row.status not in {"draft", "pending_approval"} for row in self.expected.values()
            )
        else:
            assert expected is not None
            legal = wrong == "none" and (
                expected.status == "draft"
                if action in {"update", "submit"}
                else expected.status == "pending_approval" and expected.maker != ACTORS[actor]
            )
        before = self.evidence()
        try:
            result = self.command(action, actor, key, title, decision, wrong)
        except HTTPException as exc:
            self.db.rollback()
            assert not legal and exc.status_code == (404 if wrong != "none" else 409)
            assert_refused_approval_unchanged(self.evidence(), before)
            return
        assert legal
        if action == "propose":
            self.expected[result.id] = ExpectedTemplate(
                "draft", ACTORS[actor], len(keys) + 1, title
            )
        else:
            assert expected is not None
            expected.audit_events += 1
            if action == "update":
                expected.title = title
            elif action == "submit":
                expected.status = "pending_approval"
            else:
                expected.status = decision
                assert result.decided_by == ACTORS[actor] and result.decided_at is not None
                assert result.decision_reason == REASON
                expected.sealed = self.content(result)
                if decision == "approved":
                    if self.current is not None:
                        self.expected[self.current].status = "superseded"
                    self.current = result.id
        assert result.status == self.expected[result.id].status

    def content(self, row: TemplateRead) -> str:
        # Approval content remains sealed when a later version supersedes it.
        return row.model_dump_json(exclude={"status", "superseded_at", "updated_at"})

    @initialize(checkpoint=template_checkpoints())
    def reach_checkpoint(self, checkpoint: str) -> None:
        self.apply("propose")
        if checkpoint == "draft":
            return
        self.apply("submit")
        if checkpoint == "pending_approval":
            return
        self.apply(
            "decide", actor=1, decision="rejected" if checkpoint == "rejected" else "approved"
        )
        if checkpoint == "superseded":
            self.apply("propose", actor=1, title="New governance")
            self.apply("submit", actor=1)
            self.apply("decide", actor=0)

    @rule(actor=st.integers(0, 1), title=stage_titles())
    def propose(self, actor: int, title: str) -> None:
        self.apply("propose", actor=actor, title=title)

    @rule(
        action=st.sampled_from(("update", "submit", "decide")),
        actor=st.integers(0, 1),
        index=st.integers(0, 20),
        title=stage_titles(),
        decision=st.sampled_from(("approved", "rejected")),
        wrong=st.sampled_from(("none", "bank", "tenant")),
    )
    def attempt(  # noqa: PLR0913 - independently generated command dimensions
        self, action: str, actor: int, index: int, title: str, decision: Decision, wrong: str
    ) -> None:
        self.apply(action, actor, index, title, decision, wrong)

    @rule()
    def advance(self) -> None:
        for index, row in enumerate(self.expected.values()):
            if row.status == "draft":
                self.apply("submit", index=index)
                return
            if row.status == "pending_approval":
                self.apply("decide", actor=1 if row.maker == USER_1 else 0, index=index)
                return
        self.apply("propose", title="Next approved version")

    @invariant()
    def history_and_effective_chain_match(self) -> None:
        listing = self.listing()
        assert len(listing.templates) == len(self.expected)
        assert sum(row.status == "approved" for row in listing.templates) <= 1
        assert sum(row.status in {"draft", "pending_approval"} for row in listing.templates) <= 1
        for row in listing.templates:
            expected = self.expected[row.id]
            assert (row.status, row.version, row.proposed_by, row.stages[0].title) == (
                expected.status,
                expected.version,
                expected.maker,
                expected.title,
            )
            assert len(success_events(self.db, row.id, self.prefix)) == expected.audit_events
            if expected.sealed is not None:
                assert_sealed_approval_unchanged(self.content(row), expected.sealed)
                assert_maker_checker(row.proposed_by, row.decided_by)
            if row.status == "superseded":
                assert row.superseded_at is not None
        if self.current is None:
            assert listing.effective_source == (
                "platform_default" if self.kind == "filing" else "framework_default"
            )
        else:
            assert listing.effective_source == "bank_template"
            selected = next(row for row in listing.templates if row.id == self.current)
            assert tuple(stage.model_dump_json() for stage in listing.effective_stages) == tuple(
                stage.model_dump_json() for stage in selected.stages
            )
        assert self.listing("bank").templates == []
        assert self.listing("tenant").templates == []


@pytest.mark.parametrize("kind", ("filing", "icaap"))
def test_template_versions_obey_maker_checker_and_sealing(
    db_session: Session,
    grant_client: TestClient,
    kind: Kind,
) -> None:
    run_database_machine(lambda: TemplateMachine(db_session, kind))
