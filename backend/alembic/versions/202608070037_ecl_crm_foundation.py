"""IFRS 9 ECL engine + CRM supervisory haircuts (product.md §Phase 2 items 8/9)

Revision ID: 202608070037
Revises: 202608070036

Two new effective-dated parameter tables on the RegulatoryParameterMixin
shape (approval evidence + generations):

- ``param_ecl_assumption``: PD/LGD per loan segment + IFRS 9 stage (segment
  ``ALL`` is the fallback; stage 3 contributes LGD only — PD is 100% by
  definition for credit-impaired exposures).
- ``param_crm_haircut``: Basel II comprehensive-approach supervisory
  haircuts per CRM collateral class. A class with no row gets zero
  recognition in credit RWA — a haircut is never invented.

Also widens ``ck_bank_financial_facts_fact_group`` with the two derived
fact groups the engines consume: ``ecl_exposure`` (staged EAD buckets) and
``crm_collateral`` (collateral/guarantee values per loan family + class).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608070037"
down_revision = "202608070036"
branch_labels = None
depends_on = None

FACTS_TABLE = "bank_financial_facts"
FACTS_CONSTRAINT = "ck_bank_financial_facts_fact_group"
FACTS_ORIGINAL = (
    "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
    "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
    "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
    "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
    "'ftp_branch', 'ftp_nmd')"
)
FACTS_WIDENED = (
    "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
    "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
    "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
    "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
    "'ftp_branch', 'ftp_nmd', 'ecl_exposure', 'crm_collateral')"
)

# Post-platform-ID-epoch policy form: text comparison, never a ::uuid cast.
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"


def _param_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=8), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("approved_by", sa.String(length=120), nullable=False),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _force_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )


def upgrade() -> None:
    op.create_table(
        "param_ecl_assumption",
        *_param_columns(),
        sa.Column("segment", sa.String(length=60), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("pd_pct", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("lgd_pct", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint("stage IN (1, 2, 3)", name="ck_param_ecl_assumption_stage"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "segment",
            "stage",
            "effective_from",
            name="uq_param_ecl_assumption_scope",
        ),
    )
    op.create_table(
        "param_crm_haircut",
        *_param_columns(),
        sa.Column("collateral_class", sa.String(length=80), nullable=False),
        sa.Column("haircut_pct", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "collateral_class",
            "effective_from",
            name="uq_param_crm_haircut_scope",
        ),
    )
    for table in ("param_ecl_assumption", "param_crm_haircut"):
        _force_rls(table)

    op.drop_constraint(FACTS_CONSTRAINT, FACTS_TABLE, type_="check")
    op.create_check_constraint(FACTS_CONSTRAINT, FACTS_TABLE, FACTS_WIDENED)


def downgrade() -> None:
    op.drop_constraint(FACTS_CONSTRAINT, FACTS_TABLE, type_="check")
    op.create_check_constraint(FACTS_CONSTRAINT, FACTS_TABLE, FACTS_ORIGINAL)
    for table in ("param_crm_haircut", "param_ecl_assumption"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
