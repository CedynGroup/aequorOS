from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import (
    AuditEvent,
    Document,
    DocumentChunk,
    RiskCase,
    RiskFinding,
    RiskFindingEvidence,
    StoredObject,
)
from app.services import assessments, findings
from tests.api.helpers import ORG_2
from tests.services.factories import ServiceFactories


def create_finding(
    db_session: Session, factories: ServiceFactories
) -> tuple[ServiceFactories, RiskFinding]:
    ctx = factories.ctx
    document = factories.documents.create_parsed()
    assessment = factories.assessments.create(document.case_id)
    assessments.run_assessment(db_session, ctx, assessment.id)
    finding = db_session.scalar(select(RiskFinding))
    assert finding is not None
    return factories, finding


def test_update_finding_rejects_generated_fields(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories, finding = create_finding(db_session, service_factories)
    command = findings.UpdateFindingCommand(
        update_data={"title": "Nope"},
        fields_set={"title"},
    )

    with pytest.raises(HTTPException) as exc:
        findings.update_finding(db_session, factories.ctx, finding.id, command)
    assert exc.value.status_code == 400


def test_update_finding_status_records_audit_event(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories, finding = create_finding(db_session, service_factories)
    command = findings.UpdateFindingCommand(
        fields_set={"status", "disposition_reason"},
        update_data={
            "status": "accepted",
            "disposition_reason": "Confirmed",
        },
    )

    updated = findings.update_finding(db_session, factories.ctx, finding.id, command)

    event_types = set(db_session.scalars(select(AuditEvent.event_type)))
    assert updated.status == "acknowledged"
    assert "finding.status_changed" in event_types


def attach_finding_evidence(db_session: Session, finding: RiskFinding) -> None:
    chunk = db_session.scalar(
        select(DocumentChunk).where(DocumentChunk.organization_id == finding.organization_id)
    )
    assert chunk is not None
    db_session.add(
        RiskFindingEvidence(
            organization_id=finding.organization_id,
            finding_id=finding.id,
            document_id=chunk.document_id,
            document_chunk_id=chunk.id,
            page_number=chunk.page_start,
            quote=chunk.text,
            locator={"chunk_index": chunk.chunk_index},
        )
    )
    db_session.commit()


def test_list_finding_evidence_includes_document_and_chunk_metadata(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories, finding = create_finding(db_session, service_factories)
    attach_finding_evidence(db_session, finding)

    evidence = findings.list_finding_evidence(db_session, factories.ctx, finding.id)

    assert len(evidence) == 1
    assert evidence[0].document is not None
    assert evidence[0].document.filename == "financials.pdf"
    assert evidence[0].chunk is not None
    assert evidence[0].chunk.chunk_index == 0


def test_list_finding_evidence_does_not_join_cross_org_document_metadata(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories, finding = create_finding(db_session, service_factories)
    attach_finding_evidence(db_session, finding)
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

    result = findings.list_finding_evidence(db_session, factories.ctx, finding.id)

    assert len(result) == 1
    assert result[0].document_id == other_document.id
    assert result[0].document is None
    assert result[0].chunk is None
