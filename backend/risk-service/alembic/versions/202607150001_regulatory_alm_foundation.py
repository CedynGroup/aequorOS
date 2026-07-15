"""regulatory alm foundation

Revision ID: 202607150001
Revises: 202607140001
Create Date: 2026-07-15 12:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607150001"
down_revision = "202607140001"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"
TABLES = (
    "banks",
    "bank_reporting_periods",
    "bank_financial_facts",
    "param_lcr_runoff_rate",
    "param_nsfr_weight",
    "param_risk_weight",
    "param_stress_shock",
    "param_capital_threshold",
)


def _parameter_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=8), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("approved_by", sa.String(length=120), nullable=False),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "banks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("short_name", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=8), nullable=False),
        sa.Column("license_type", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_banks_id_organization_id"),
    )
    op.create_index("ix_banks_organization_id", "banks", ["organization_id"])

    op.create_table(
        "bank_reporting_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_bank_reporting_periods_status"),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bank_id", "period_end", name="uq_bank_reporting_periods_bank_period_end"
        ),
        sa.UniqueConstraint(
            "id", "organization_id", "bank_id", name="uq_bank_reporting_periods_id_org_bank"
        ),
    )
    op.create_index(
        "ix_bank_reporting_periods_org_bank_period_end",
        "bank_reporting_periods",
        ["organization_id", "bank_id", "period_end"],
    )

    op.create_table(
        "bank_financial_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reporting_period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_group", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("risk_weight_code", sa.String(length=16), nullable=True),
        sa.Column("hqla_level", sa.String(length=8), nullable=True),
        sa.Column("ccf_pct", sa.Numeric(9, 6), nullable=True),
        sa.Column("rate_pct", sa.Numeric(9, 6), nullable=True),
        sa.Column("income_year", sa.Integer(), nullable=True),
        sa.Column("capital_tier", sa.String(length=8), nullable=True),
        sa.Column(
            "is_deduction",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
            "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
            "'deposit_behavior')",
            name="ck_bank_financial_facts_fact_group",
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reporting_period_id",
            "fact_group",
            "category",
            name="uq_bank_financial_facts_period_group_category",
        ),
    )
    op.create_index(
        "ix_bank_financial_facts_org_bank_period_group",
        "bank_financial_facts",
        ["organization_id", "bank_id", "reporting_period_id", "fact_group"],
    )

    op.create_table(
        "param_lcr_runoff_rate",
        *_parameter_columns(),
        sa.Column("flow_direction", sa.String(length=8), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("rate_pct", sa.Numeric(9, 6), nullable=False),
        sa.CheckConstraint(
            "flow_direction IN ('outflow', 'inflow')",
            name="ck_param_lcr_runoff_rate_flow_direction",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "flow_direction",
            "category",
            "effective_from",
            name="uq_param_lcr_runoff_rate_scope",
        ),
    )

    op.create_table(
        "param_nsfr_weight",
        *_parameter_columns(),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("weight_pct", sa.Numeric(9, 6), nullable=False),
        sa.CheckConstraint("side IN ('asf', 'rsf')", name="ck_param_nsfr_weight_side"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "side",
            "category",
            "effective_from",
            name="uq_param_nsfr_weight_scope",
        ),
    )

    op.create_table(
        "param_risk_weight",
        *_parameter_columns(),
        sa.Column("risk_weight_code", sa.String(length=16), nullable=False),
        sa.Column("weight_pct", sa.Numeric(9, 6), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "risk_weight_code",
            "effective_from",
            name="uq_param_risk_weight_scope",
        ),
    )

    op.create_table(
        "param_stress_shock",
        *_parameter_columns(),
        sa.Column("module", sa.String(length=16), nullable=False),
        sa.Column("scenario_code", sa.String(length=40), nullable=False),
        sa.Column("shock_key", sa.String(length=80), nullable=False),
        sa.Column("shock_value", sa.Numeric(18, 8), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "module IN ('liquidity', 'capital', 'forecast')",
            name="ck_param_stress_shock_module",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "module",
            "scenario_code",
            "shock_key",
            "effective_from",
            name="uq_param_stress_shock_scope",
        ),
    )

    op.create_table(
        "param_capital_threshold",
        *_parameter_columns(),
        sa.Column("threshold_code", sa.String(length=40), nullable=False),
        sa.Column("value_pct", sa.Numeric(12, 6), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "threshold_code",
            "effective_from",
            name="uq_param_capital_threshold_scope",
        ),
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
