from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.api.helpers import ORG_1


def db_uuid(session: Session, value: UUID) -> str:
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        return value.hex
    return str(value)


def normalize_json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    assert isinstance(value, dict)
    return value


def test_phase_1_database_defaults_are_defined(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    case_id = db_uuid(db_session, uuid4())
    stored_object_id = db_uuid(db_session, uuid4())
    document_id = db_uuid(db_session, uuid4())
    chunk_id = db_uuid(db_session, uuid4())
    extraction_id = db_uuid(db_session, uuid4())
    assessment_id = db_uuid(db_session, uuid4())
    run_id = db_uuid(db_session, uuid4())
    finding_id = db_uuid(db_session, uuid4())
    evidence_id = db_uuid(db_session, uuid4())
    job_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:case_id, :org_id, 'Vendor case', 'vendor', 'active', :now, :now)
            """
        ),
        {"case_id": case_id, "org_id": org_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO stored_objects
              (id, organization_id, provider, bucket, object_key, status, created_at)
            VALUES
              (:stored_object_id, :org_id, 's3', 'risk-local', 'object-key', 'available', :now)
            """
        ),
        {"stored_object_id": stored_object_id, "org_id": org_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO documents
              (
                id,
                organization_id,
                case_id,
                stored_object_id,
                filename,
                status,
                created_at,
                updated_at
              )
            VALUES
              (
                :document_id,
                :org_id,
                :case_id,
                :stored_object_id,
                'financials.pdf',
                'uploaded',
                :now,
                :now
              )
            """
        ),
        {
            "document_id": document_id,
            "org_id": org_id,
            "case_id": case_id,
            "stored_object_id": stored_object_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO document_chunks
              (id, organization_id, document_id, chunk_index, text, created_at)
            VALUES
              (:chunk_id, :org_id, :document_id, 0, 'placeholder text', :now)
            """
        ),
        {"chunk_id": chunk_id, "org_id": org_id, "document_id": document_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO document_extractions
              (
                id,
                organization_id,
                document_id,
                extraction_type,
                schema_version,
                status,
                created_at
              )
            VALUES
              (
                :extraction_id,
                :org_id,
                :document_id,
                'phase_1',
                '1',
                'completed',
                :now
              )
            """
        ),
        {
            "extraction_id": extraction_id,
            "org_id": org_id,
            "document_id": document_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO risk_assessments
              (id, organization_id, case_id, name, assessment_type, status, created_at, updated_at)
            VALUES
              (
                :assessment_id,
                :org_id,
                :case_id,
                'Initial vendor risk assessment',
                'vendor_risk',
                'draft',
                :now,
                :now
              )
            """
        ),
        {"assessment_id": assessment_id, "org_id": org_id, "case_id": case_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO risk_assessment_runs
              (id, organization_id, assessment_id, status, created_at)
            VALUES
              (:run_id, :org_id, :assessment_id, 'queued', :now)
            """
        ),
        {"run_id": run_id, "org_id": org_id, "assessment_id": assessment_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO risk_findings
              (
                id,
                organization_id,
                case_id,
                assessment_id,
                run_id,
                risk_type,
                title,
                summary,
                severity,
                created_at,
                updated_at
              )
            VALUES
              (
                :finding_id,
                :org_id,
                :case_id,
                :assessment_id,
                :run_id,
                'documentation_gap',
                'Missing covenant support',
                'The file is missing covenant details.',
                'medium',
                :now,
                :now
              )
            """
        ),
        {
            "finding_id": finding_id,
            "org_id": org_id,
            "case_id": case_id,
            "assessment_id": assessment_id,
            "run_id": run_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO risk_finding_evidence
              (id, organization_id, finding_id, document_id, document_chunk_id, created_at)
            VALUES
              (:evidence_id, :org_id, :finding_id, :document_id, :chunk_id, :now)
            """
        ),
        {
            "evidence_id": evidence_id,
            "org_id": org_id,
            "finding_id": finding_id,
            "document_id": document_id,
            "chunk_id": chunk_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO jobs
              (id, organization_id, job_type, status, queued_at)
            VALUES
              (:job_id, :org_id, 'document_parse', 'queued', :now)
            """
        ),
        {"job_id": job_id, "org_id": org_id, "now": now},
    )
    db_session.commit()

    row = db_session.execute(
        text(
            """
            SELECT
              risk_cases.metadata AS case_metadata,
              documents.source,
              documents.parse_status,
              document_chunks.metadata AS chunk_metadata,
              document_extractions.extracted_json,
              risk_assessments.input_snapshot,
              risk_assessments.config_snapshot,
              risk_assessment_runs.summary AS run_summary,
              risk_findings.status AS finding_status,
              risk_finding_evidence.locator,
              jobs.attempts,
              jobs.max_attempts,
              jobs.progress
            FROM risk_cases
            JOIN documents ON documents.case_id = risk_cases.id
            JOIN document_chunks ON document_chunks.document_id = documents.id
            JOIN document_extractions ON document_extractions.document_id = documents.id
            JOIN risk_assessments ON risk_assessments.case_id = risk_cases.id
            JOIN risk_assessment_runs
              ON risk_assessment_runs.assessment_id = risk_assessments.id
            JOIN risk_findings ON risk_findings.run_id = risk_assessment_runs.id
            JOIN risk_finding_evidence ON risk_finding_evidence.finding_id = risk_findings.id
            JOIN jobs ON jobs.organization_id = risk_cases.organization_id
            WHERE risk_cases.id = :case_id
            """
        ),
        {"case_id": case_id},
    ).one()

    assert normalize_json(row.case_metadata) == {}
    assert row.source == "upload"
    assert row.parse_status == "not_started"
    assert normalize_json(row.chunk_metadata) == {}
    assert normalize_json(row.extracted_json) == {}
    assert normalize_json(row.input_snapshot) == {}
    assert normalize_json(row.config_snapshot) == {}
    assert normalize_json(row.run_summary) == {}
    assert row.finding_status == "open"
    assert normalize_json(row.locator) == {}
    assert row.attempts == 0
    assert row.max_attempts == 3
    assert normalize_json(row.progress) == {}
