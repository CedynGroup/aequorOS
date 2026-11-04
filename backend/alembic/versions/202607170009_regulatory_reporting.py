"""regulatory reporting hub

Revision ID: 202607170009
Revises: 202607170008
Create Date: 2026-07-17 23:30:00.000000

Regulatory Reporting & Submission Hub schema slice
(docs/regulatory_reporting.md §3): immutable versioned return packages with
their append-only artifacts, maker-checker approvals, and submission events,
plus per-bank channel configs with write-only encrypted credentials. All
tables are tenant-scoped with RLS enabled + forced; a partial unique index
keeps exactly one non-superseded package version per
(org, bank, return_code, reporting_date).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607170009"
down_revision = "202607170008"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"

PACKAGE_STATUSES = (
    "'draft', 'generated', 'validated', 'pending_approval', 'approved', "
    "'submitted', 'acknowledged', 'rejected', 'superseded'"
)
RETURN_FAMILIES = "'liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress'"
RETURN_FREQUENCIES = "'monthly', 'quarterly', 'semiannual', 'annual'"
ARTIFACT_KINDS = "'xlsx', 'csv', 'pdf'"
SUBMISSION_CHANNELS = "'orass_sandbox', 'email', 'manual'"
SUBMISSION_EVENTS = "'submitted', 'status_poll', 'acknowledged', 'rejected'"
APPROVAL_ACTIONS = "'requested', 'approved', 'rejected'"

NEW_TABLES = (
    "regulatory_packages",
    "regulatory_package_artifacts",
    "regulatory_package_approvals",
    "regulatory_submission_events",
    "regulatory_channel_configs",
)


def upgrade() -> None:
    op.create_table(
        "regulatory_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("return_family", sa.String(length=20), nullable=False),
        sa.Column("return_code", sa.String(length=40), nullable=False),
        sa.Column("reporting_date", sa.Date(), nullable=False),
        sa.Column("frequency", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "source_runs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("validation_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("generated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({PACKAGE_STATUSES})",
            name="ck_regulatory_packages_status",
        ),
        sa.CheckConstraint(
            f"return_family IN ({RETURN_FAMILIES})",
            name="ck_regulatory_packages_return_family",
        ),
        sa.CheckConstraint(
            f"frequency IN ({RETURN_FREQUENCIES})",
            name="ck_regulatory_packages_frequency",
        ),
        sa.CheckConstraint("version >= 1", name="ck_regulatory_packages_version"),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_regulatory_packages_id_org"),
    )
    op.create_index(
        "ix_regulatory_packages_org_bank_reporting_date",
        "regulatory_packages",
        ["organization_id", "bank_id", "reporting_date"],
    )
    op.create_index(
        "ix_regulatory_packages_org_bank_status",
        "regulatory_packages",
        ["organization_id", "bank_id", "status"],
    )
    op.create_index(
        "uq_regulatory_packages_current",
        "regulatory_packages",
        ["organization_id", "bank_id", "return_code", "reporting_date"],
        unique=True,
        postgresql_where=sa.text("status != 'superseded'"),
    )

    op.create_table(
        "regulatory_package_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("object_path", sa.String(length=512), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"kind IN ({ARTIFACT_KINDS})",
            name="ck_regulatory_package_artifacts_kind",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_regulatory_package_artifacts_id_org"
        ),
    )
    op.create_index(
        "ix_regulatory_package_artifacts_org_package",
        "regulatory_package_artifacts",
        ["organization_id", "package_id"],
    )

    op.create_table(
        "regulatory_package_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=12), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"action IN ({APPROVAL_ACTIONS})",
            name="ck_regulatory_package_approvals_action",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_regulatory_package_approvals_id_org"
        ),
    )
    op.create_index(
        "ix_regulatory_package_approvals_org_package",
        "regulatory_package_approvals",
        ["organization_id", "package_id"],
    )

    op.create_table(
        "regulatory_submission_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"channel IN ({SUBMISSION_CHANNELS})",
            name="ck_regulatory_submission_events_channel",
        ),
        sa.CheckConstraint(
            f"event IN ({SUBMISSION_EVENTS})",
            name="ck_regulatory_submission_events_event",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_regulatory_submission_events_id_org"
        ),
    )
    op.create_index(
        "ix_regulatory_submission_events_org_package",
        "regulatory_submission_events",
        ["organization_id", "package_id"],
    )

    op.create_table(
        "regulatory_channel_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("credential_ciphertext", sa.Text(), nullable=True),
        sa.Column("credential_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"channel IN ({SUBMISSION_CHANNELS})",
            name="ck_regulatory_channel_configs_channel",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_regulatory_channel_configs_id_org"),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "channel",
            name="uq_regulatory_channel_configs_scope",
        ),
    )
    op.create_index(
        "ix_regulatory_channel_configs_org_bank",
        "regulatory_channel_configs",
        ["organization_id", "bank_id"],
    )

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation
            ON {table}
            USING (organization_id = {TENANT_ID_EXPR})
            WITH CHECK (organization_id = {TENANT_ID_EXPR})
            """
        )


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index(
        "ix_regulatory_channel_configs_org_bank", table_name="regulatory_channel_configs"
    )
    op.drop_table("regulatory_channel_configs")
    op.drop_index(
        "ix_regulatory_submission_events_org_package", table_name="regulatory_submission_events"
    )
    op.drop_table("regulatory_submission_events")
    op.drop_index(
        "ix_regulatory_package_approvals_org_package", table_name="regulatory_package_approvals"
    )
    op.drop_table("regulatory_package_approvals")
    op.drop_index(
        "ix_regulatory_package_artifacts_org_package", table_name="regulatory_package_artifacts"
    )
    op.drop_table("regulatory_package_artifacts")
    op.drop_index("uq_regulatory_packages_current", table_name="regulatory_packages")
    op.drop_index("ix_regulatory_packages_org_bank_status", table_name="regulatory_packages")
    op.drop_index(
        "ix_regulatory_packages_org_bank_reporting_date", table_name="regulatory_packages"
    )
    op.drop_table("regulatory_packages")
