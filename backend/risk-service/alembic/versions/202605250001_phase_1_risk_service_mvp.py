"""phase 1 risk service mvp

Revision ID: 202605250001
Revises: 202605240001
Create Date: 2026-05-25 00:01:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202605250001"
down_revision = "202605240001"
branch_labels = None
depends_on = None


def jsonb_default() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "risk_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("case_type", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=True),
        sa.Column("subject_name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        *timestamps(),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_risk_cases_organization_id_status", "risk_cases", ["organization_id", "status"]
    )
    op.create_index(
        "ix_risk_cases_organization_id_created_at",
        "risk_cases",
        ["organization_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "stored_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("version_id", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "bucket",
            "object_key",
            name="uq_stored_objects_organization_id_bucket_object_key",
        ),
    )

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stored_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'upload'")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "parse_status", sa.Text(), nullable=False, server_default=sa.text("'not_started'")
        ),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["risk_cases.id"]),
        sa.ForeignKeyConstraint(["stored_object_id"], ["stored_objects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_documents_organization_id_case_id", "documents", ["organization_id", "case_id"]
    )
    op.create_index(
        "ix_documents_organization_id_status", "documents", ["organization_id", "status"]
    )
    op.create_index(
        "ix_documents_organization_id_parse_status",
        "documents",
        ["organization_id", "parse_status"],
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_id_chunk_index"
        ),
    )

    op.create_table(
        "document_extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "extracted_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "risk_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("assessment_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column(
            "config_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["case_id"], ["risk_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "risk_assessment_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("input_hash", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["risk_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "risk_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("risk_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("likelihood", sa.Text(), nullable=True),
        sa.Column("impact", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("disposition_reason", sa.Text(), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["assessment_id"], ["risk_assessments.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["risk_cases.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["risk_assessment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_risk_findings_organization_id_case_id", "risk_findings", ["organization_id", "case_id"]
    )
    op.create_index(
        "ix_risk_findings_organization_id_assessment_id",
        "risk_findings",
        ["organization_id", "assessment_id"],
    )
    op.create_index(
        "ix_risk_findings_organization_id_status", "risk_findings", ["organization_id", "status"]
    )
    op.create_index(
        "ix_risk_findings_organization_id_severity",
        "risk_findings",
        ["organization_id", "severity"],
    )

    op.create_table(
        "risk_finding_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column(
            "locator",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("relevance", sa.Numeric(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_chunk_id"], ["document_chunks.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["finding_id"], ["risk_findings.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column(
            "progress",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("risk_finding_evidence")
    op.drop_index("ix_risk_findings_organization_id_severity", table_name="risk_findings")
    op.drop_index("ix_risk_findings_organization_id_status", table_name="risk_findings")
    op.drop_index("ix_risk_findings_organization_id_assessment_id", table_name="risk_findings")
    op.drop_index("ix_risk_findings_organization_id_case_id", table_name="risk_findings")
    op.drop_table("risk_findings")
    op.drop_table("risk_assessment_runs")
    op.drop_table("risk_assessments")
    op.drop_table("document_extractions")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_organization_id_parse_status", table_name="documents")
    op.drop_index("ix_documents_organization_id_status", table_name="documents")
    op.drop_index("ix_documents_organization_id_case_id", table_name="documents")
    op.drop_table("documents")
    op.drop_table("stored_objects")
    op.drop_index("ix_risk_cases_organization_id_created_at", table_name="risk_cases")
    op.drop_index("ix_risk_cases_organization_id_status", table_name="risk_cases")
    op.drop_table("risk_cases")
