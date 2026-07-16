"""job queue live engine

Revision ID: 202607170005
Revises: 202607170004
Create Date: 2026-07-17 20:00:00.000000

Extends the ``jobs`` table into a dispatchable live-engine queue (bank_id,
payload, run_after, coalesce_key + supporting indexes) and adds the two
always-fresh live surfaces: ``live_metrics`` (one upserted baseline row per
bank/period/module) and ``live_findings`` (the reconciled open limit breaches
that feed alerts). Both new tables carry the standard tenant RLS policy; the
new ``jobs`` columns inherit the policy already on that table.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607170005"
down_revision = "202607170004"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"
LIVE_TABLES = ("live_metrics", "live_findings")
MODULE_CHECK = "module IN ('liquidity', 'capital', 'irr', 'fx', 'ftp', 'forecast')"


def upgrade() -> None:
    op.add_column("jobs", sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("jobs", sa.Column("run_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("coalesce_key", sa.String(length=120), nullable=True))
    op.create_index("ix_jobs_status_run_after", "jobs", ["status", "run_after"])
    op.create_index(
        "ix_jobs_organization_id_coalesce_key", "jobs", ["organization_id", "coalesce_key"]
    )

    op.create_table(
        "live_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reporting_period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module", sa.String(length=16), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("computed_from_input_hash", sa.String(length=64), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(MODULE_CHECK, name="ck_live_metrics_module"),
        sa.CheckConstraint(
            "status IN ('green', 'amber', 'red', 'na')", name="ck_live_metrics_status"
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "reporting_period_id",
            "module",
            name="uq_live_metrics_org_bank_period_module",
        ),
    )
    op.create_index(
        "ix_live_metrics_org_bank_period",
        "live_metrics",
        ["organization_id", "bank_id", "reporting_period_id"],
    )

    op.create_table(
        "live_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reporting_period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module", sa.String(length=16), nullable=False),
        sa.Column("rule_id", sa.String(length=120), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("metric", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(MODULE_CHECK, name="ck_live_findings_module"),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_live_findings_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'needs_review', 'superseded')",
            name="ck_live_findings_status",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_live_findings_open",
        "live_findings",
        ["organization_id", "bank_id", "reporting_period_id", "module", "rule_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_live_findings_org_bank_status",
        "live_findings",
        ["organization_id", "bank_id", "status"],
    )

    for table in LIVE_TABLES:
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
    for table in reversed(LIVE_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_live_findings_org_bank_status", table_name="live_findings")
    op.drop_index("uq_live_findings_open", table_name="live_findings")
    op.drop_table("live_findings")
    op.drop_index("ix_live_metrics_org_bank_period", table_name="live_metrics")
    op.drop_table("live_metrics")

    op.drop_index("ix_jobs_organization_id_coalesce_key", table_name="jobs")
    op.drop_index("ix_jobs_status_run_after", table_name="jobs")
    op.drop_column("jobs", "coalesce_key")
    op.drop_column("jobs", "run_after")
    op.drop_column("jobs", "payload")
    op.drop_column("jobs", "bank_id")
