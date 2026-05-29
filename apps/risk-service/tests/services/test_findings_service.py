from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.features import assessments_service, findings_service
from app.models import (
    AuditEvent,
    Document,
    DocumentChunk,
    RiskCase,
    RiskFinding,
    RiskFindingEvidence,
    StoredObject,
)
from tests.api.helpers import ORG_2
from tests.conftest import FakeStorage
from tests.services.factories import ServiceFactories, tenant_context


def create_finding(
    db_session: Session, fake_storage: FakeStorage
) -> tuple[ServiceFactories, RiskFinding]:
    settings = get_settings()
    ctx = tenant_context()
    factories = ServiceFactories(db_session, fake_storage, settings, ctx)
    case = factories.create_case()
    factories.create_parsed_document(case.id)
    assessment = factories.create_assessment(case.id)
    assessments_service.run_assessment(db_session, ctx, assessment.id)
    finding = db_session.scalar(select(RiskFinding))
    assert finding is not None
    return factories, finding


def test_update_finding_rejects_generated_fields(
    db_session: Session,
    fake_storage: FakeStorage,
) -> None:
    factories, finding = create_finding(db_session, fake_storage)
    payload = SimpleNamespace(
        model_fields_set={"title"},
        model_dump=lambda exclude_unset: {"title": "Nope"},
    )

    with pytest.raises(HTTPException) as exc:
        findings_service.update_finding(db_session, factories.ctx, finding.id, payload)
    assert exc.value.status_code == 400


def test_update_finding_status_records_audit_event(
    db_session: Session,
    fake_storage: FakeStorage,
) -> None:
    factories, finding = create_finding(db_session, fake_storage)
    payload = SimpleNamespace(
        model_fields_set={"status", "disposition_reason"},
        model_dump=lambda exclude_unset: {
            "status": "accepted",
            "disposition_reason": "Confirmed",
        },
    )

    updated = findings_service.update_finding(db_session, factories.ctx, finding.id, payload)

    event_types = set(db_session.scalars(select(AuditEvent.event_type)))
    assert updated.status == "accepted"
    assert "finding.status_changed" in event_types


def test_list_finding_evidence_includes_document_and_chunk_metadata(
    db_session: Session,
    fake_storage: FakeStorage,
) -> None:
    factories, finding = create_finding(db_session, fake_storage)

    evidence = findings_service.list_finding_evidence(db_session, factories.ctx, finding.id)

    assert len(evidence) == 1
    assert evidence[0].document is not None
    assert evidence[0].document["filename"] == "financials.pdf"
    assert evidence[0].chunk is not None
    assert evidence[0].chunk["chunk_index"] == 0


def test_list_finding_evidence_does_not_join_cross_org_document_metadata(
    db_session: Session,
    fake_storage: FakeStorage,
) -> None:
    factories, finding = create_finding(db_session, fake_storage)
    other_case = RiskCase(
        organization_id=ORG_2,
        title="Other org case",
        case_type="vendor",
        status="active",
        metadata_={},
    )
    other_object = StoredObject(
        organization_id=ORG_2,
        provider="s3",
        bucket="risk-local",
        object_key="orgs/other/documents/original",
        status="available",
    )
    db_session.add_all([other_case, other_object])
    db_session.flush()
    other_document = Document(
        organization_id=ORG_2,
        case_id=other_case.id,
        stored_object_id=other_object.id,
        filename="other-org.pdf",
        status="uploaded",
        parse_status="parsed",
    )
    db_session.add(other_document)
    db_session.flush()
    other_chunk = DocumentChunk(
        organization_id=ORG_2,
        document_id=other_document.id,
        chunk_index=0,
        text="Other org evidence",
        metadata_={},
        created_at=utc_now(),
    )
    db_session.add(other_chunk)
    db_session.flush()
    evidence = db_session.scalar(
        select(RiskFindingEvidence).where(RiskFindingEvidence.finding_id == finding.id)
    )
    assert evidence is not None
    evidence.document_id = other_document.id
    evidence.document_chunk_id = other_chunk.id
    db_session.commit()

    result = findings_service.list_finding_evidence(db_session, factories.ctx, finding.id)

    assert len(result) == 1
    assert result[0].document_id == other_document.id
    assert result[0].document is None
    assert result[0].chunk is None
