"""scenario and assumption framework

Revision ID: 202607130001
Revises: 202607120001
Create Date: 2026-07-13 00:01:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607130001"
down_revision = "202607120001"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"
TABLES = ("risk_scenarios", "scenario_assumptions", "scenario_assumption_history")


def jsonb_default() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "risk_scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scenario_type", sa.String(length=40), nullable=False),
        sa.Column("copied_from_scenario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scenario_type IN ('baseline', 'downside', 'custom')",
            name="ck_risk_scenarios_type",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", "case_id", name="uq_risk_scenarios_id_organization_id_case_id"
        ),
    )
    op.create_index("ix_risk_scenarios_case_id", "risk_scenarios", ["case_id"])
    op.create_index(
        "uq_risk_scenarios_active_default",
        "risk_scenarios",
        ["organization_id", "case_id", "scenario_type"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL AND scenario_type != 'custom'"),
    )

    op.create_table(
        "scenario_assumptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("review_status", sa.String(length=40), nullable=False, server_default="draft"),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('growth', 'expenses', 'cash_flow_timing', 'credit_usage', "
            "'repayment_behavior', 'other')",
            name="ck_scenario_assumptions_category",
        ),
        sa.CheckConstraint(
            "review_status IN ('draft', 'reviewed')",
            name="ck_scenario_assumptions_review_status",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id", "organization_id", "case_id"],
            ["risk_scenarios.id", "risk_scenarios.organization_id", "risk_scenarios.case_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenario_assumptions_scenario_id", "scenario_assumptions", ["scenario_id"])
    op.create_index(
        "uq_scenario_assumptions_key",
        "scenario_assumptions",
        ["organization_id", "scenario_id", "key"],
        unique=True,
    )

    op.create_table(
        "scenario_assumption_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assumption_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("changed_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id", "organization_id"], ["risk_cases.id", "risk_cases.organization_id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scenario_assumption_history_assumption_id",
        "scenario_assumption_history",
        ["assumption_id"],
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
