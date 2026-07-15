"""regulatory runs

Revision ID: 202607150002
Revises: 202607150001
Create Date: 2026-07-15 12:30:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607150002"
down_revision = "202607150001"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"
TABLES = (
    "regulatory_runs",
    "regulatory_metric_results",
    "regulatory_line_items",
    "regulatory_validations",
)
RUNS_FK = [
    "regulatory_runs.id",
    "regulatory_runs.organization_id",
    "regulatory_runs.bank_id",
]


def upgrade() -> None:
    op.create_table(
        "regulatory_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reporting_period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module", sa.String(length=16), nullable=False),
        sa.Column("scenario_code", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("engine_version", sa.String(length=80), nullable=False),
        sa.Column("input_schema_version", sa.String(length=40), nullable=False),
        sa.Column("output_schema_version", sa.String(length=40), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "module IN ('liquidity', 'capital')",
            name="ck_regulatory_runs_module",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_regulatory_runs_status",
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
            "id", "organization_id", "bank_id", name="uq_regulatory_runs_id_org_bank"
        ),
    )
    op.create_index(
        "ix_regulatory_runs_org_bank_module_scenario",
        "regulatory_runs",
        ["organization_id", "bank_id", "module", "scenario_code"],
    )
    op.create_index(
        "ix_regulatory_runs_org_input_hash",
        "regulatory_runs",
        ["organization_id", "input_hash"],
    )

    op.create_table(
        "regulatory_metric_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_code", sa.String(length=60), nullable=False),
        sa.Column("metric_value", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("threshold_min", sa.Numeric(20, 6), nullable=True),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("unit IN ('pct', 'ghs')", name="ck_regulatory_metric_results_unit"),
        sa.CheckConstraint(
            "status IN ('green', 'amber', 'red', 'na')",
            name="ck_regulatory_metric_results_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "organization_id", "bank_id"],
            RUNS_FK,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "metric_code", name="uq_regulatory_metric_results_run_metric"
        ),
    )
    op.create_index("ix_regulatory_metric_results_run_id", "regulatory_metric_results", ["run_id"])

    op.create_table(
        "regulatory_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section", sa.String(length=24), nullable=False),
        sa.Column("line_code", sa.String(length=60), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("exposure_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("rate_pct", sa.Numeric(9, 6), nullable=True),
        sa.Column("weighted_amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "section IN ('hqla', 'outflow', 'inflow', 'asf', 'rsf', 'credit_rwa', "
            "'market_rwa', 'operational_rwa', 'capital_component', 'ratio')",
            name="ck_regulatory_line_items_section",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "organization_id", "bank_id"],
            RUNS_FK,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "section", "line_code", name="uq_regulatory_line_items_run_section_line"
        ),
    )
    op.create_index("ix_regulatory_line_items_run_id", "regulatory_line_items", ["run_id"])

    op.create_table(
        "regulatory_validations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_code", sa.String(length=60), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "severity IN ('error', 'warning', 'info')",
            name="ck_regulatory_validations_severity",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "organization_id", "bank_id"],
            RUNS_FK,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "rule_code", name="uq_regulatory_validations_run_rule"),
    )
    op.create_index("ix_regulatory_validations_run_id", "regulatory_validations", ["run_id"])

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

    op.drop_index("ix_regulatory_validations_run_id", table_name="regulatory_validations")
    op.drop_table("regulatory_validations")
    op.drop_index("ix_regulatory_line_items_run_id", table_name="regulatory_line_items")
    op.drop_table("regulatory_line_items")
    op.drop_index("ix_regulatory_metric_results_run_id", table_name="regulatory_metric_results")
    op.drop_table("regulatory_metric_results")
    op.drop_index("ix_regulatory_runs_org_input_hash", table_name="regulatory_runs")
    op.drop_index("ix_regulatory_runs_org_bank_module_scenario", table_name="regulatory_runs")
    op.drop_table("regulatory_runs")
