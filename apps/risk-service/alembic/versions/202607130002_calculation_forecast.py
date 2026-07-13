"""calculation runs and balance sheet forecast

Revision ID: 202607130002
Revises: 202607130001
Create Date: 2026-07-13 12:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607130002"
down_revision = "202607130001"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"
TABLES = ("calculation_runs", "calculation_forecast_periods")


def upgrade() -> None:
    op.create_table(
        "calculation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rerun_of_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("engine_version", sa.String(length=80), nullable=False),
        sa.Column("input_schema_version", sa.String(length=40), nullable=False),
        sa.Column("output_schema_version", sa.String(length=40), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("forecast_periods", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_calculation_runs_status",
        ),
        sa.CheckConstraint("forecast_periods BETWEEN 1 AND 12", name="ck_calculation_runs_periods"),
        sa.ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id", "organization_id", "case_id"],
            ["risk_scenarios.id", "risk_scenarios.organization_id", "risk_scenarios.case_id"],
        ),
        sa.ForeignKeyConstraint(
            ["rerun_of_run_id", "organization_id", "case_id"],
            ["calculation_runs.id", "calculation_runs.organization_id", "calculation_runs.case_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", "case_id", name="uq_calculation_runs_id_org_case"
        ),
    )
    op.create_index(
        "ix_calculation_runs_case_scenario", "calculation_runs", ["case_id", "scenario_id"]
    )
    op.create_index(
        "ix_calculation_runs_input_hash",
        "calculation_runs",
        ["organization_id", "input_hash"],
    )

    op.create_table(
        "calculation_forecast_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_number", sa.Integer(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_assets", sa.Numeric(20, 4), nullable=False),
        sa.Column("total_liabilities", sa.Numeric(20, 4), nullable=False),
        sa.Column("total_equity", sa.Numeric(20, 4), nullable=False),
        sa.Column("cash", sa.Numeric(20, 4), nullable=False),
        sa.Column("projected_inflows", sa.Numeric(20, 4), nullable=False),
        sa.Column("projected_outflows", sa.Numeric(20, 4), nullable=False),
        sa.Column("credit_draw", sa.Numeric(20, 4), nullable=False),
        sa.Column("debt_repayment", sa.Numeric(20, 4), nullable=False),
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint("period_number > 0", name="ck_calculation_forecast_periods_number"),
        sa.ForeignKeyConstraint(
            ["run_id", "organization_id", "case_id"],
            ["calculation_runs.id", "calculation_runs.organization_id", "calculation_runs.case_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "period_number", name="uq_calculation_forecast_run_period"),
    )
    op.create_index(
        "ix_calculation_forecast_periods_run_id", "calculation_forecast_periods", ["run_id"]
    )

    for table in TABLES:
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
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)
