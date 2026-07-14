"""capital projection MVP

Revision ID: 202607130003
Revises: 202607130002
Create Date: 2026-07-13 12:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607130003"
down_revision = "202607130002"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "capital_projections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("calculation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("engine_version", sa.String(80), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("reporting_currency", sa.String(3), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_capital_projections_status",
        ),
        sa.ForeignKeyConstraint(
            ["calculation_run_id", "organization_id", "case_id"],
            ["calculation_runs.id", "calculation_runs.organization_id", "calculation_runs.case_id"],
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id", "organization_id", "case_id"],
            ["risk_scenarios.id", "risk_scenarios.organization_id", "risk_scenarios.case_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", "case_id", name="uq_capital_projections_id_org_case"
        ),
    )
    op.create_index(
        "ix_capital_projections_case_scenario",
        "capital_projections",
        ["organization_id", "case_id", "scenario_id"],
    )
    op.create_table(
        "capital_indicators",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("projection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("forecast_period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_number", sa.Integer(), nullable=False),
        sa.Column("equity", sa.Numeric(20, 4), nullable=False),
        sa.Column("equity_to_assets_ratio", sa.Numeric(12, 8), nullable=False),
        sa.Column("liabilities_to_assets_ratio", sa.Numeric(12, 8), nullable=False),
        sa.Column("equity_change", sa.Numeric(20, 4), nullable=False),
        sa.Column("pressure_level", sa.String(24), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint("period_number > 0", name="ck_capital_indicators_period_number"),
        sa.CheckConstraint(
            "pressure_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_capital_indicators_pressure_level",
        ),
        sa.ForeignKeyConstraint(
            ["projection_id", "organization_id", "case_id"],
            [
                "capital_projections.id",
                "capital_projections.organization_id",
                "capital_projections.case_id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "projection_id", "period_number", name="uq_capital_indicator_projection_period"
        ),
    )
    op.create_index(
        "ix_capital_indicators_projection", "capital_indicators", ["projection_id", "period_number"]
    )
    op.create_table(
        "capital_projection_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("projection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["projection_id", "organization_id", "case_id"],
            [
                "capital_projections.id",
                "capital_projections.organization_id",
                "capital_projections.case_id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["finding_id"], ["risk_findings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("projection_id", "finding_id", name="uq_capital_projection_finding"),
    )
    op.create_index(
        "ix_capital_projection_findings_projection",
        "capital_projection_findings",
        ["projection_id"],
    )
    for table in ("capital_projections", "capital_indicators", "capital_projection_findings"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING (organization_id = {TENANT_ID_EXPR}) "
            f"WITH CHECK (organization_id = {TENANT_ID_EXPR})"
        )


def downgrade() -> None:
    for table in ("capital_projection_findings", "capital_indicators", "capital_projections"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_capital_projection_findings_projection", table_name="capital_projection_findings"
    )
    op.drop_table("capital_projection_findings")
    op.drop_index("ix_capital_indicators_projection", table_name="capital_indicators")
    op.drop_table("capital_indicators")
    op.drop_index("ix_capital_projections_case_scenario", table_name="capital_projections")
    op.drop_table("capital_projections")
