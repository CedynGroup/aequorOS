"""Current canonical-derived facts for the live Treasury plane.

Revision ID: 202608190021
Revises: 202608190020
Create Date: 2026-08-19 13:00:00.000000

The table is intentionally separate from `bank_financial_facts`: it holds only
one current generation per bank and is replaced by pipeline refreshes. Period
facts and regulatory runs remain historical/governance structures.
"""

from alembic import op
import sqlalchemy as sa


revision = "202608190021"
down_revision = "202608190020"
branch_labels = None
depends_on = None

_FACT_GROUP_CHECK = (
    "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
    "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
    "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
    "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
    "'ftp_branch', 'ftp_nmd', 'ecl_exposure', 'crm_collateral', 'cashflow')"
)


def upgrade() -> None:
    op.create_table(
        "current_financial_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("source_as_of_date", sa.Date(), nullable=False),
        sa.Column("source_generation", sa.Integer(), nullable=False),
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
        sa.Column("is_deduction", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_FACT_GROUP_CHECK, name="ck_current_financial_facts_fact_group"),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "fact_group", "category",
            name="uq_current_financial_facts_bank_group_category",
        ),
    )
    op.create_index(
        "ix_current_financial_facts_org_bank_group",
        "current_financial_facts",
        ["organization_id", "bank_id", "fact_group"],
    )


def downgrade() -> None:
    op.drop_index("ix_current_financial_facts_org_bank_group", table_name="current_financial_facts")
    op.drop_table("current_financial_facts")
