"""immutable liquidity analysis results

Revision ID: 202607140001
Revises: 202607130003
Create Date: 2026-07-14 12:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607140001"
down_revision = "202607130003"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"
TABLE = "liquidity_analysis_results"
FINDING_RUN_INDEX = "ix_risk_findings_liquidity_calculation_run"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_version", sa.String(length=80), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id", "organization_id", "case_id"],
            ["calculation_runs.id", "calculation_runs.organization_id", "calculation_runs.case_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_liquidity_analysis_results_run_id"),
    )
    op.create_index("ix_liquidity_analysis_results_case_id", TABLE, ["case_id"])
    op.execute(
        f"""
        CREATE INDEX {FINDING_RUN_INDEX}
        ON risk_findings (
            organization_id,
            case_id,
            ((details -> 'liquidity' ->> 'calculation_run_id'))
        )
        WHERE risk_type = 'liquidity_risk'
          AND source = 'deterministic_rule'
          AND details -> 'liquidity' ->> 'workflow_id' = 'liquidity_analysis'
        """
    )
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {TABLE}_tenant_isolation
        ON {TABLE}
        USING (organization_id = {TENANT_ID_EXPR})
        WITH CHECK (organization_id = {TENANT_ID_EXPR})
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index(FINDING_RUN_INDEX, table_name="risk_findings")
    op.drop_table(TABLE)
