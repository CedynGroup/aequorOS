from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentExtraction, RiskFinding, RiskScore
from app.services import assessments, findings
from tests.services.factories import ServiceFactories


def test_create_assessment_snapshots_current_documents(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    case = factories.create_case()
    document = factories.create_uploaded_document(case.id)

    assessment = factories.create_assessment(case.id)

    assert assessment.input_snapshot == {"document_ids": [str(document.id)]}


def test_run_assessment_creates_finding_when_parsed_evidence_exists(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.create_case()
    factories.create_parsed_document(case.id)
    assessment = factories.create_assessment(case.id)

    result = assessments.run_assessment(db_session, ctx, assessment.id)

    findings = list(db_session.scalars(select(RiskFinding)))
    assert result.status == "completed"
    assert len(findings) == 1
    assert findings[0].risk_type == "documentation_gap"
    assert findings[0].rule_id == "missing_structured_data"
    assert findings[0].source == "deterministic_rule"
    assert findings[0].organization_id == ctx.organization_id


def test_run_assessment_scores_structured_data_and_updates_case(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.create_case(
        metadata={
            "structured_data": {
                "vendor_criticality": "critical",
                "debt_to_ebitda": 6.2,
                "cash_runway_months": 2,
            }
        }
    )
    assessment = factories.create_assessment(case.id)

    result = assessments.run_assessment(db_session, ctx, assessment.id)

    findings = list(db_session.scalars(select(RiskFinding)))
    scores = list(db_session.scalars(select(RiskScore)))
    run = assessments.get_run_or_404(db_session, ctx.organization_id, result.run_id)
    assert result.status == "completed"
    assert run.engine_version == "deterministic_v1"
    assert run.summary["risk_score"] == 100
    assert run.summary["risk_level"] == "critical"
    assert run.summary["findings_created"] == 3
    assert run.summary["score_id"] == str(scores[0].id)
    assert run.input_hash == run.summary["input_hash"]
    assert scores[0].score == 100
    assert scores[0].risk_level == "critical"
    assert scores[0].run_id == result.run_id
    assert scores[0].input_hash == run.input_hash
    assert len(scores[0].rule_results) == 3
    assert run.summary["input_snapshot"] == {
        "structured_data": {
            "vendor_criticality": "critical",
            "debt_to_ebitda": 6.2,
            "cash_runway_months": 2,
        }
    }
    assert case.risk_score == 100
    assert case.risk_level == "critical"
    assert case.status == "in_review"
    assert {finding.rule_id for finding in findings} == {
        "vendor_criticality",
        "elevated_debt_to_ebitda",
        "low_cash_runway",
    }


def test_run_assessment_normalizes_structured_data_aliases(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.create_case(
        metadata={
            "structured_data": {
                "vendorCriticality": "high",
                "debtToEbitda": "4.8",
                "cashRunwayMonths": "5",
                "requiredDocuments": ["soc2", "financials"],
                "providedDocuments": ["financials"],
            }
        }
    )
    assessment = factories.create_assessment(case.id)

    result = assessments.run_assessment(db_session, ctx, assessment.id)

    run = assessments.get_run_or_404(db_session, ctx.organization_id, result.run_id)
    assert run.summary["risk_score"] == 95
    assert run.summary["input_snapshot"] == {
        "structured_data": {
            "required_documents": ["soc2", "financials"],
            "provided_documents": ["financials"],
            "vendor_criticality": "high",
            "debt_to_ebitda": 4.8,
            "cash_runway_months": 5.0,
        }
    }


def test_unrecognized_structured_data_is_treated_as_missing_reviewed_input(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.create_case(metadata={"structured_data": {"cashRunwayMonthz": 2}})
    assessment = factories.create_assessment(case.id)

    result = assessments.run_assessment(db_session, ctx, assessment.id)

    run = assessments.get_run_or_404(db_session, ctx.organization_id, result.run_id)
    finding = db_session.scalar(select(RiskFinding))
    assert finding is not None
    assert run.summary["risk_score"] == 20
    assert run.summary["input_snapshot"] == {"structured_data": {}}
    assert finding.rule_id == "missing_structured_data"


def test_rerun_scoring_supersedes_prior_generated_findings(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.create_case(metadata={})
    first_assessment = factories.create_assessment(case.id)

    first_result = assessments.run_assessment(db_session, ctx, first_assessment.id)
    first_run = assessments.get_run_or_404(db_session, ctx.organization_id, first_result.run_id)
    assert first_run.summary["findings_created"] == 1

    case.metadata_ = {"structured_data": {"vendor_criticality": "low"}}
    second_assessment = assessments.create_assessment(
        db_session,
        ctx,
        SimpleNamespace(case_id=case.id, assessment_type="vendor_risk", name="Rescore"),
    )

    second_result = assessments.run_assessment(db_session, ctx, second_assessment.id)
    second_run = assessments.get_run_or_404(db_session, ctx.organization_id, second_result.run_id)

    findings = list(db_session.scalars(select(RiskFinding)))
    open_findings = [finding for finding in findings if finding.status in {"open", "needs_review"}]
    assert second_run.summary["risk_score"] == 0
    assert second_run.summary["findings_created"] == 0
    assert open_findings == []
    assert {finding.status for finding in findings} == {"superseded"}


def test_rerun_scoring_keeps_reviewed_generated_findings(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.create_case(metadata={})
    first_assessment = factories.create_assessment(case.id)
    assessments.run_assessment(db_session, ctx, first_assessment.id)
    first_finding = db_session.scalar(select(RiskFinding))
    assert first_finding is not None
    first_finding.status = "acknowledged"
    db_session.commit()

    case.metadata_ = {"structured_data": {"vendor_criticality": "low"}}
    second_assessment = assessments.create_assessment(
        db_session,
        ctx,
        SimpleNamespace(case_id=case.id, assessment_type="vendor_risk", name="Rescore"),
    )
    assessments.run_assessment(db_session, ctx, second_assessment.id)

    db_session.refresh(first_finding)
    assert first_finding.status == "acknowledged"
    assert db_session.scalar(select(RiskFinding).where(RiskFinding.status == "superseded")) is None


def test_rerun_scoring_does_not_supersede_manual_findings(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.create_case(metadata={})
    manual = findings.create_case_finding(
        db_session,
        ctx,
        case.id,
        findings.CreateFindingCommand(
            risk_type="documentation_gap",
            title="Manual concern",
            summary="Reviewer-entered issue.",
            rationale=None,
            severity="medium",
            likelihood=None,
            impact=None,
            confidence=None,
            details={},
        ),
    )
    assessment = factories.create_assessment(case.id)

    assessments.run_assessment(db_session, ctx, assessment.id)

    db_session.refresh(manual)
    assert manual.status == "open"
    assert manual.source == "manual"


def test_scoring_collects_structured_data_with_later_sources_taking_precedence(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.create_case(
        metadata={"structured_data": {"vendor_criticality": "high", "debt_to_ebitda": 5}}
    )
    document = factories.create_uploaded_document(case.id)
    assessment = factories.create_assessment(case.id)
    assessment.input_snapshot = {
        "structured_data": {"vendor_criticality": "critical", "debt_to_ebitda": 3}
    }
    db_session.add(
        DocumentExtraction(
            organization_id=ctx.organization_id,
            document_id=document.id,
            extraction_type="structured_data",
            schema_version="1",
            status="completed",
            extracted_json={
                "structured_data": {
                    "vendor_criticality": "low",
                    "cash_runway_months": 2,
                }
            },
        )
    )
    db_session.commit()

    result = assessments.run_assessment(db_session, ctx, assessment.id)

    run = assessments.get_run_or_404(db_session, ctx.organization_id, result.run_id)
    assert run.summary["input_snapshot"] == {
        "structured_data": {
            "vendor_criticality": "low",
            "debt_to_ebitda": 3.0,
            "cash_runway_months": 2.0,
        }
    }
    assert run.summary["risk_score"] == 40
    assert run.summary["risk_level"] == "medium"


def test_run_assessment_rejects_archived_cases(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    ctx = factories.ctx
    case = factories.create_case()
    assessment = factories.create_assessment(case.id)
    case.status = "archived"

    with pytest.raises(HTTPException) as exc:
        assessments.run_assessment(db_session, ctx, assessment.id)

    assert exc.value.status_code == 409
