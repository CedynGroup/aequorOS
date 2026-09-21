"""The family hooks, the ICAAP validation rules, the review chain templates,
and the two guards on the non-production framework directory."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import IcaapSettings
from app.models import RegulatoryPackage
from app.models.icaap import IcaapCycle, IcaapStageDecision
from app.schemas.icaap import (
    IcaapStageInput,
    IcaapWorkflowTemplateCreate,
    IcaapWorkflowTemplateDecision,
    IcaapWorkflowTemplateSubmit,
)
from app.services.icaap import cycles as cycles_service
from app.services.icaap import filing, filing_hooks, validation_rules, workflow_templates
from app.services.regulatory_reporting import family_hooks
from tests.services.icaap.p3_support import (
    FRAMEWORK_ROOT,
    access_for,
    make_user,
)
from tests.services.icaap.test_p3_clone_and_disclosure import _frozen


def _detail(exc: HTTPException) -> dict[str, object]:
    detail = exc.detail
    assert isinstance(detail, dict)
    return detail


def _error(exc: HTTPException) -> str:
    return str(_detail(exc)["error_code"])


#: A chain the platform can actually run, for the tests that only need one.
_STAGES = [
    IcaapStageInput(
        seq=1,
        stage_key="preparation",
        title="Preparation",
        decision_kind="prepare",
        officer_titles=[],
    ),
    IcaapStageInput(
        seq=2,
        stage_key="brc_approval",
        title="Board Risk Committee approval",
        decision_kind="approve",
        freeze_on_approve=True,
    ),
    IcaapStageInput(seq=3, stage_key="board", title="Board approval", decision_kind="attest"),
]


def _record_review(db: Session, cycle_id, *, status: str):  # noqa: ANN001, ANN202
    """One internal-audit review row, written directly.

    The services correctly refuse a WRITE to a frozen cycle, and what is under
    test here is the READ filter — which row a supervisor's list returns — so
    the row is inserted rather than driven through a lifecycle the freeze has
    already closed.
    """
    from datetime import date as _date  # noqa: PLC0415

    from app.db.base import utc_now  # noqa: PLC0415
    from app.models.icaap_risk_capital import IcaapAuditReview  # noqa: PLC0415

    cycle = db.get(IcaapCycle, cycle_id)
    assert cycle is not None
    row = IcaapAuditReview(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        status=status,
        review_kind="internal_audit",
        reviewer_function="Internal Audit",
        scope="The ICAAP process end to end.",
        frequency_statement="Annually.",
        performed_on=_date(cycle.as_of_date.year, 6, 30),
        reviewed_cycle_id=cycle.id,
        overall_opinion="satisfactory",
        findings=[],
        independence_statement="Internal Audit reports to the Board Audit Committee.",
        recorded_by=cycle.created_by,
        finalised_at=None if status == "draft" else utc_now(),
        finalised_by=None if status == "draft" else cycle.created_by,
        row_rev=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _record_ai_suggestion(db: Session, cycle_id):  # noqa: ANN001, ANN202
    """One queued AI suggestion, written directly, for the same reason."""
    from uuid import uuid4  # noqa: PLC0415

    from app.models.icaap import IcaapSection  # noqa: PLC0415
    from app.models.icaap_ai import IcaapAiSuggestion  # noqa: PLC0415

    cycle = db.get(IcaapCycle, cycle_id)
    assert cycle is not None
    section = db.scalars(
        select(IcaapSection)
        .where(IcaapSection.cycle_id == cycle.id)
        .order_by(IcaapSection.position)
    ).first()
    assert section is not None
    row = IcaapAiSuggestion(
        id=uuid4(),
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        section_id=section.id,
        section_key=section.section_key,
        cycle_round=1,
        status="queued",
        requested_by=cycle.created_by,
        fact_sheet_mode="standard",
        fact_sheet={"facts": []},
        fact_sheet_sha256="b" * 64,
        fact_bindings={},
        entity_keys=[],
        framework_code=cycle.framework_code,
        framework_version=cycle.framework_version,
        framework_sha256="c" * 64,
        prompt_version="v1",
        prompt_sha256="d" * 64,
        model_requested="a-model",
        effort="medium",
        max_output_tokens=1000,
        fallbacks_mode="default",
        consent_version="v1",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class TestTheExtraFrameworkDirectory:
    def test_a_deployed_environment_refuses_to_load_an_unreviewed_framework(self) -> None:
        """An allow-list, not "not production": staging is a deployment too."""
        settings = IcaapSettings.model_validate({"ICAAP_EXTRA_FRAMEWORKS_DIR": str(FRAMEWORK_ROOT)})
        for env in ("production", "staging"):
            with pytest.raises(ValueError, match="ICAAP_EXTRA_FRAMEWORKS_DIR"):
                settings.extra_framework_roots(env)
        assert settings.extra_framework_roots("local") == (FRAMEWORK_ROOT,)
        assert settings.extra_framework_roots("test") == (FRAMEWORK_ROOT,)

    def test_an_unset_directory_is_no_directory_in_every_environment(self) -> None:
        settings = IcaapSettings.model_validate({"ICAAP_EXTRA_FRAMEWORKS_DIR": None})
        assert settings.extra_framework_roots("production") == ()
        blank = IcaapSettings.model_validate({"ICAAP_EXTRA_FRAMEWORKS_DIR": "  "})
        assert blank.extra_framework_roots("production") == ()


class TestFamilyHooks:
    def test_the_icaap_family_is_the_one_the_generic_plane_dispatches_to(self) -> None:
        hooks = family_hooks.for_family("icaap")
        assert hooks is not None
        assert hooks.family == "icaap"
        assert family_hooks.for_family("bsd") is None

    def test_the_required_documents_come_from_the_frozen_snapshot(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, _cycle, out = _frozen(db)
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        required = family_hooks.required_attachments(db, access.ctx, package)
        # The framework's own requirement, at both gates. Not relaxable by a
        # signing policy (D-031): dropping a signature slot changes signatures.
        assert required == {"senior_management_report": 1, "board_resolution": 1}

    def test_a_package_no_longer_linked_to_its_cycle_cannot_be_submitted(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        from app.services.icaap import post_freeze  # noqa: PLC0415
        from tests.services.icaap.p3_support import return_payload  # noqa: PLC0415

        db = canonical_book
        access, reviewer, _a, cycle, out = _frozen(db)
        post_freeze.return_cycle(
            db,
            reviewer,
            cycle.id,
            return_payload(
                db,
                reviewer,
                cycle.id,
                return_to_seq=1,
                reason="The Board asked for a correction.",
            ),
        )
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        with pytest.raises(HTTPException) as caught:
            family_hooks.ensure_submittable(db, access.ctx, package, object())
        assert _error(caught.value) == "icaap_package_not_current"

    def test_the_board_records_its_approval_by_resolution_when_it_does_not_sign(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """D-043: the Board slot ships OFF, so the resolution is the evidence."""
        db = canonical_book
        access, _r, _a, cycle, out = _frozen(db)
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        filing_hooks.HOOKS.on_transition(
            db, access.ctx, package, previous="approved", new_status="submitted"
        )
        db.commit()
        row = db.get(IcaapCycle, cycle.id)
        assert row is not None
        assert row.status == "submitted"
        recorded = db.scalar(
            select(IcaapStageDecision).where(
                IcaapStageDecision.cycle_id == cycle.id,
                IcaapStageDecision.decision == "attested_by_resolution",
            )
        )
        assert recorded is not None
        # It quotes the digest the freeze recorded, never a placeholder.
        frozen_decision = db.scalar(
            select(IcaapStageDecision).where(
                IcaapStageDecision.cycle_id == cycle.id,
                IcaapStageDecision.decision == "frozen",
            )
        )
        assert frozen_decision is not None
        assert recorded.review_digest == frozen_decision.review_digest

    def test_a_superseded_package_supersedes_its_cycle(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, out = _frozen(db)
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        filing_hooks.HOOKS.on_package_superseded(db, access.ctx, package)
        db.commit()
        row = db.get(IcaapCycle, cycle.id)
        assert row is not None
        assert row.status == "superseded"
        assert row.superseded_at is not None


class TestValidationRules:
    def test_a_complete_frozen_report_raises_no_error(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        _access, _r, _a, _cycle, out = _frozen(db)
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        findings = validation_rules.findings(db, package)
        assert [entry for entry in findings if entry["severity"] == "ERROR"] == []

    @pytest.mark.parametrize(
        ("block_type", "rule"),
        [
            ("capital_position", "icaap.as_of"),
            ("capital_reconciliation", "icaap.pillar2_reconciliation"),
        ],
    )
    def test_a_report_with_no_capital_figures_at_all_is_an_error(
        self,
        canonical_book: Session,
        extra_frameworks: None,
        block_type: str,
        rule: str,
    ) -> None:
        """The absence of a figure cannot be quieter than a wrong one (audit F2).

        Both rules used to skip the block they could not find: ``_as_of``
        iterates only over blocks that exist, and ``_pillar2_reconciliation``
        returned ``[]``. Together with the freeze gate's warning, that meant a
        report with no capital figures in it passed validation, while a capital
        position one month stale was an ERROR.
        """
        db = canonical_book
        _access, _r, _a, _cycle, out = _frozen(db)
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None

        # Non-vacuity: the report IS complete before the block is taken out.
        clean = validation_rules.findings(db, package)
        assert [entry for entry in clean if entry["severity"] == "ERROR"] == []

        snapshot = dict(package.snapshot)
        block = dict(snapshot["metadata"]["icaap"])
        block["blocks"] = [
            entry for entry in block["blocks"] if entry.get("block_type") != block_type
        ]
        snapshot["metadata"] = {**snapshot["metadata"], "icaap": block}
        package.snapshot = snapshot

        findings = validation_rules.findings(db, package)
        assert any(
            entry["rule"] == rule and entry["severity"] == "ERROR" for entry in findings
        ), findings

    def test_an_unanswered_checklist_item_is_an_error(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        _access, _r, _a, _cycle, out = _frozen(db)
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        snapshot = dict(package.snapshot)
        block = dict(snapshot["metadata"]["icaap"])
        sections = [dict(entry) for entry in block["sections"]]
        sections[0] = {
            **sections[0],
            "requirements": [{"item_id": "x", "status": "open", "reason": None, "citations": []}],
        }
        block["sections"] = sections
        snapshot["metadata"] = {**snapshot["metadata"], "icaap": block}
        package.snapshot = snapshot
        findings = validation_rules.findings(db, package)
        assert any(
            entry["rule"] == "icaap.requirements" and entry["severity"] == "ERROR"
            for entry in findings
        )

    def test_a_missing_freeze_document_is_an_error(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        _access, _r, _a, _cycle, out = _frozen(db)
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        snapshot = dict(package.snapshot)
        block = {**snapshot["metadata"]["icaap"], "attachments": []}
        snapshot["metadata"] = {**snapshot["metadata"], "icaap": block}
        package.snapshot = snapshot
        findings = validation_rules.findings(db, package)
        assert any(entry["rule"] == "icaap.freeze_attachments" for entry in findings)

    def test_a_package_that_did_not_come_from_a_freeze_says_so(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        _access, _r, _a, _cycle, out = _frozen(db)
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        package.snapshot = {**package.snapshot, "metadata": {}}
        findings = validation_rules.findings(db, package)
        assert findings[0]["severity"] == "ERROR"
        assert "frozen" in findings[0]["detail"]

    def test_a_pending_governed_figure_is_reported_but_does_not_block(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """D-024 §5: a value awaiting stakeholder confirmation is labelled, not hidden."""
        db = canonical_book
        _access, _r, _a, _cycle, out = _frozen(db)
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        findings = validation_rules.findings(db, package)
        pending = [entry for entry in findings if entry["rule"] == "icaap.parameters_pending"]
        assert pending
        assert all(entry["severity"] == "WARNING" for entry in pending)


class TestFilingView:
    def test_an_unfrozen_cycle_has_nothing_to_file(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        from tests.services.icaap.test_p3_freeze import _build  # noqa: PLC0415

        db = canonical_book
        access, _r, _a, cycle = _build(db)
        view = filing.get_filing(db, access, cycle.id)
        assert view.submittable is False
        assert [entry.code for entry in view.blockers] == ["cycle_not_frozen"]

    def test_a_frozen_cycle_reports_its_outstanding_documents(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        view = filing.get_filing(db, access, cycle.id)
        assert view.package is not None
        assert view.submittable is False
        # The Board resolution has not been attached to the PACKAGE yet.
        outstanding = {entry.kind for entry in view.attachments if not entry.satisfied}
        assert "board_resolution" in outstanding
        # The blocker is whatever the SUBMISSION GATE says, because this view
        # calls that gate rather than re-implementing it. In a deployment that
        # cannot sign at all, the signing refusal comes first and that is the
        # honest answer — the screen must not say "ready" about a submission
        # the server will refuse.
        assert [entry.code for entry in view.blockers] == [view.blockers[0].code]
        assert view.blockers[0].code in {"attachments_missing", "signing_not_configured"}


class TestWorkflowTemplates:
    def test_a_bank_chain_is_approved_by_four_eyes_and_then_governs(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access = access_for(db)
        framework = cycles_service.default_framework(db, access)
        stages = [
            IcaapStageInput(
                seq=1,
                stage_key="preparation",
                title="Preparation",
                decision_kind="prepare",
                officer_titles=[],
            ),
            IcaapStageInput(
                seq=2,
                stage_key="alco_review",
                title="ALCO review",
                decision_kind="review",
                officer_titles=[],
            ),
            IcaapStageInput(
                seq=3,
                stage_key="brc_approval",
                title="Board Risk Committee approval",
                decision_kind="approve",
                freeze_on_approve=True,
            ),
            IcaapStageInput(
                seq=4,
                stage_key="board",
                title="Board approval",
                decision_kind="attest",
            ),
        ]
        proposed = workflow_templates.propose_template(
            db,
            access,
            framework,
            IcaapWorkflowTemplateCreate(
                stages=stages, reason="Our ALCO reviews before the committee."
            ),
        )
        workflow_templates.submit_template(
            db, access, proposed.id, IcaapWorkflowTemplateSubmit(reason="Ready for approval.")
        )
        with pytest.raises(HTTPException) as caught:
            workflow_templates.decide_template(
                db,
                access,
                proposed.id,
                IcaapWorkflowTemplateDecision(
                    decision="approved", reason="Approving my own proposal."
                ),
            )
        assert _error(caught.value) == "maker_checker"
        checker = access_for(db, make_user(db, email="gov@example.com", name="Gov"))
        approved = workflow_templates.decide_template(
            db,
            checker,
            proposed.id,
            IcaapWorkflowTemplateDecision(
                decision="approved", reason="The chain matches our governance."
            ),
        )
        assert approved.status == "approved"
        listing = workflow_templates.list_templates(db, access, framework)
        assert listing.effective_source == "bank_template"
        assert [stage.stage_key for stage in listing.effective_stages] == [
            "preparation",
            "alco_review",
            "brc_approval",
            "board",
        ]

    def test_a_chain_the_platform_cannot_run_is_refused_with_its_reason(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access = access_for(db)
        framework = cycles_service.default_framework(db, access)
        no_attest = [
            IcaapStageInput(
                seq=1,
                stage_key="preparation",
                title="Preparation",
                decision_kind="prepare",
            ),
            IcaapStageInput(
                seq=2,
                stage_key="approval",
                title="Approval",
                decision_kind="approve",
                freeze_on_approve=True,
            ),
        ]
        with pytest.raises(HTTPException) as caught:
            workflow_templates.propose_template(
                db,
                access,
                framework,
                IcaapWorkflowTemplateCreate(
                    stages=no_attest, reason="A chain with no Board attestation."
                ),
            )
        assert _error(caught.value) == "stage_attest_required"


class TestExaminerReads:
    def test_a_supervisor_reads_the_frozen_chain_and_can_do_nothing_to_it(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """The examiner branch is P1's; P3's reads must not step around it.

        Every P3 read goes through ``get_cycle_or_404``, which limits an
        impersonated supervisor to cycles that were frozen — so nothing a Board
        has not seen is ever served — and none of them resolves an acting user,
        which is what would raise on an impersonated session.
        """
        from dataclasses import replace  # noqa: PLC0415

        from app.services.icaap import filing, workflow  # noqa: PLC0415

        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        examiner = replace(access, examiner=True)
        stages = workflow.get_stages(db, examiner, cycle.id)
        assert [entry.stage_key for entry in stages.stages][0] == "preparation"
        assert stages.viewer.can_decide is False
        assert stages.viewer.can_freeze is False
        assert stages.viewer.can_submit is False
        assert filing.get_filing(db, examiner, cycle.id).package is not None

    def test_a_supervisor_never_reads_a_review_chain_nobody_has_approved(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """Audit S-7: the one ICAAP read that had no examiner branch at all.

        ``workflow_templates._rows`` filtered organisation and bank and nothing
        else, so ``GET /banks/{id}/icaap/workflow-templates`` served a
        supervisor the chains the bank is still arguing about — a draft it has
        not submitted, a proposal awaiting four eyes, one that was rejected.
        Changing who approves an ICAAP is itself an approval; until it has one,
        the chain governs nothing and is not the bank's governance.
        """
        from dataclasses import replace  # noqa: PLC0415

        db = canonical_book
        access = access_for(db)
        framework = cycles_service.default_framework(db, access)
        checker = access_for(db, make_user(db, email="chain-checker@example.com", name="Check"))

        in_force = workflow_templates.propose_template(
            db, access, framework, IcaapWorkflowTemplateCreate(stages=_STAGES, reason="Our chain.")
        )
        workflow_templates.submit_template(
            db, access, in_force.id, IcaapWorkflowTemplateSubmit(reason="Ready for approval.")
        )
        workflow_templates.decide_template(
            db,
            checker,
            in_force.id,
            IcaapWorkflowTemplateDecision(decision="approved", reason="Matches our governance."),
        )
        proposed = workflow_templates.propose_template(
            db,
            access,
            framework,
            IcaapWorkflowTemplateCreate(stages=_STAGES, reason="Thinking about a change."),
        )

        examiner = replace(access, examiner=True)
        seen = workflow_templates.list_templates(db, examiner, framework)
        assert [row.id for row in seen.templates] == [in_force.id]
        assert {row.status for row in seen.templates} == {"approved"}
        # The chain a cycle would pin is still answered — that IS the approved one.
        assert seen.effective_source == "bank_template"
        # The bank's own officers keep seeing what they are working on.
        theirs = workflow_templates.list_templates(db, access, framework)
        assert {row.id for row in theirs.templates} == {in_force.id, proposed.id}

    def test_a_supervisor_never_reads_an_unfinished_internal_audit_review(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """A draft review is an opinion its own author has not issued yet.

        Findings are still being written and the overall opinion can still
        change, so a supervisor quoting one would be quoting a working paper.
        Finalising is the act that makes it the institution's answer.
        """
        from dataclasses import replace  # noqa: PLC0415

        from app.services.icaap import audit_reviews  # noqa: PLC0415

        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        unfinished = _record_review(db, cycle.id, status="draft")
        issued = _record_review(db, cycle.id, status="finalised")
        examiner = replace(access, examiner=True)

        seen = audit_reviews.list_reviews(db, examiner, cycle.id).reviews
        assert [row.id for row in seen] == [issued.id]
        assert seen[0].status == "finalised"
        # The bank's own officers still see the one their auditor is writing.
        assert {row.id for row in audit_reviews.list_reviews(db, access, cycle.id).reviews} == {
            unfinished.id,
            issued.id,
        }

    def test_a_supervisor_reads_no_machine_drafts_at_all(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """An AI suggestion is working material whatever became of it.

        The ones a preparer accepted are in the section text the Board approved,
        where the supervisor reads them; the rest are proposals the bank
        declined or has not looked at. Neither the queue nor a direct read
        serves them.
        """
        from dataclasses import replace  # noqa: PLC0415

        from app.services.icaap import ai_drafting  # noqa: PLC0415

        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        suggestion = _record_ai_suggestion(db, cycle.id)
        examiner = replace(access, examiner=True)

        assert ai_drafting.list_drafts(db, examiner, cycle.id, suggestion.section_key).items == []
        with pytest.raises(HTTPException) as caught:
            ai_drafting.get_draft(
                db, examiner, cycle.id, suggestion.section_key, suggestion.id
            )
        assert caught.value.status_code == 404
        # Unchanged for the bank's own preparer.
        mine = ai_drafting.list_drafts(db, access, cycle.id, suggestion.section_key)
        assert [row.id for row in mine.items] == [suggestion.id]
