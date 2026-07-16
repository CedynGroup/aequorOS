from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin, utc_now


class RiskCase(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "risk_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'in_review', 'completed', 'archived')",
            name="ck_risk_cases_status",
        ),
        CheckConstraint(
            "decision IS NULL OR "
            "decision IN ('approved', 'rejected', 'needs_more_info', 'escalated')",
            name="ck_risk_cases_decision",
        ),
        CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_cases_risk_level",
        ),
        CheckConstraint(
            "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
            name="ck_risk_cases_risk_score",
        ),
        Index("ix_risk_cases_organization_id_status", "organization_id", "status"),
        Index("ix_risk_cases_organization_id_created_at", "organization_id", "created_at"),
        Index(
            "ix_risk_cases_organization_id_assigned_to",
            "organization_id",
            "assigned_to_user_id",
        ),
        UniqueConstraint("id", "organization_id", name="uq_risk_cases_id_organization_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    case_type: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    subject_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    assigned_to_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(40), nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scoring_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RiskCaseDecision(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "risk_case_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected', 'needs_more_info', 'escalated')",
            name="ck_risk_case_decisions_decision",
        ),
        Index("ix_risk_case_decisions_organization_id_case_id", "organization_id", "case_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_cases.id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    previous_decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class StoredObject(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "stored_objects"
    __table_args__ = (
        Index(
            "uq_stored_objects_organization_id_bucket_object_key",
            "organization_id",
            "bucket",
            "object_key",
            unique=True,
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Document(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_organization_id_case_id", "organization_id", "case_id"),
        Index("ix_documents_organization_id_status", "organization_id", "status"),
        Index("ix_documents_organization_id_parse_status", "organization_id", "parse_status"),
        UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_documents_id_organization_id_case_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_cases.id"), nullable=False
    )
    stored_object_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("stored_objects.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str] = mapped_column(
        String(40), default="upload", server_default=sql_text("'upload'"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    parse_status: Mapped[str] = mapped_column(
        String(40), default="not_started", server_default=sql_text("'not_started'"), nullable=False
    )
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentChunk(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index(
            "uq_document_chunks_document_id_chunk_index", "document_id", "chunk_index", unique=True
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class DocumentExtraction(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "document_extractions"

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id"), nullable=False
    )
    extraction_type: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    extracted_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RiskAssessment(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "risk_assessments"

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_cases.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    assessment_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class RiskAssessmentRun(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "risk_assessment_runs"

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assessment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_assessments.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    engine_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RiskScore(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "risk_scores"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="ck_risk_scores_score"),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_scores_risk_level",
        ),
        Index("ix_risk_scores_organization_id_case_id", "organization_id", "case_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_cases.id"), nullable=False
    )
    assessment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_assessments.id"), nullable=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_assessment_runs.id"), nullable=True
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False)
    scoring_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RiskFinding(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "risk_findings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'accepted', 'acknowledged', 'dismissed', "
            "'needs_review', 'resolved', 'superseded')",
            name="ck_risk_findings_status",
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_findings_severity",
        ),
        CheckConstraint(
            "source IN ('deterministic_rule', 'manual', 'imported')",
            name="ck_risk_findings_source",
        ),
        Index("ix_risk_findings_organization_id_case_id", "organization_id", "case_id"),
        Index("ix_risk_findings_organization_id_assessment_id", "organization_id", "assessment_id"),
        Index("ix_risk_findings_organization_id_status", "organization_id", "status"),
        Index("ix_risk_findings_organization_id_severity", "organization_id", "severity"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_cases.id"), nullable=False
    )
    assessment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_assessments.id"), nullable=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_assessment_runs.id"), nullable=True
    )
    risk_type: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    likelihood: Mapped[str | None] = mapped_column(String(40), nullable=True)
    impact: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str] = mapped_column(
        String(40), default="open", server_default=sql_text("'open'"), nullable=False
    )
    disposition_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(40), default="manual", server_default=sql_text("'manual'"), nullable=False
    )
    rule_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    score_impact: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class RiskFindingEvidence(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "risk_finding_evidence"

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_findings.id"), nullable=False
    )
    document_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id"), nullable=True
    )
    document_chunk_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_chunks.id"), nullable=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    locator: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    relevance: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Job(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_organization_id_coalesce_key", "organization_id", "coalesce_key"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Live-engine dispatch fields: the target bank, an arbitrary JSON payload,
    # a not-before schedule time (debounce/backoff/scheduler), and a coalesce
    # key so a burst of ingestions collapses into one queued refresh.
    bank_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    coalesce_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, server_default=sql_text("3"), nullable=False
    )
    progress: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
