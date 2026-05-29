from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.features import assessments_service
from app.models import RiskFinding
from tests.conftest import FakeStorage
from tests.services.factories import ServiceFactories, tenant_context


def test_create_assessment_snapshots_current_documents(
    db_session: Session,
    fake_storage: FakeStorage,
) -> None:
    settings = get_settings()
    ctx = tenant_context()
    factories = ServiceFactories(db_session, fake_storage, settings, ctx)
    case = factories.create_case()
    document = factories.create_uploaded_document(case.id)

    assessment = factories.create_assessment(case.id)

    assert assessment.input_snapshot == {"document_ids": [str(document.id)]}


def test_run_assessment_creates_finding_when_parsed_evidence_exists(
    db_session: Session,
    fake_storage: FakeStorage,
) -> None:
    settings = get_settings()
    ctx = tenant_context()
    factories = ServiceFactories(db_session, fake_storage, settings, ctx)
    case = factories.create_case()
    factories.create_parsed_document(case.id)
    assessment = factories.create_assessment(case.id)

    result = assessments_service.run_assessment(db_session, ctx, assessment.id)

    findings = list(db_session.scalars(select(RiskFinding)))
    assert result.status == "completed"
    assert len(findings) == 1
    assert findings[0].risk_type == "documentation_gap"
    assert findings[0].organization_id == ctx.organization_id


def test_run_assessment_completes_without_findings_when_no_evidence_exists(
    db_session: Session,
    fake_storage: FakeStorage,
) -> None:
    settings = get_settings()
    ctx = tenant_context()
    factories = ServiceFactories(db_session, fake_storage, settings, ctx)
    case = factories.create_case()
    assessment = factories.create_assessment(case.id)

    result = assessments_service.run_assessment(db_session, ctx, assessment.id)

    findings = list(db_session.scalars(select(RiskFinding)))
    run = assessments_service.get_run_or_404(db_session, ctx.organization_id, result.run_id)
    assert result.status == "completed"
    assert run.summary == {"findings_created": 0}
    assert findings == []
