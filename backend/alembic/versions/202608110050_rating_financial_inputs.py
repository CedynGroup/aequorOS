"""rating financial inputs

Revision ID: 202608110050
Revises: 202608110049
Create Date: 2026-08-11 02:00:00.000000

Allow the canonical ETL activation to persist cash-flow summary facts used by
the implied bank rating and PD scorecard.
"""

from __future__ import annotations

from alembic import op

revision = "202608110050"
down_revision = "202608110049"
branch_labels = None
depends_on = None

_OLD = (
    "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
    "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
    "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
    "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
    "'ftp_branch', 'ftp_nmd', 'ecl_exposure', 'crm_collateral')"
)
_NEW = _OLD.removesuffix(")") + ", 'cashflow')"


def upgrade() -> None:
    with op.batch_alter_table("bank_financial_facts") as batch_op:
        batch_op.drop_constraint("ck_bank_financial_facts_fact_group", type_="check")
        batch_op.create_check_constraint("ck_bank_financial_facts_fact_group", _NEW)


def downgrade() -> None:
    with op.batch_alter_table("bank_financial_facts") as batch_op:
        batch_op.drop_constraint("ck_bank_financial_facts_fact_group", type_="check")
        batch_op.create_check_constraint("ck_bank_financial_facts_fact_group", _OLD)