"""Admit capital's net credit exposure basis on both financial fact planes.

Basis: BoG CRD (June 2018) ¶98, in force.
Accounting loans, staged IFRS 9 EAD and HQLA keep their existing groups.
No tenant financial data is inserted or changed by this constraint migration.

Revision ID: 202610080085
Revises: 202610070084
"""

from __future__ import annotations

from alembic import op

revision = "202610080085"
down_revision = "202610070084"
branch_labels = None
depends_on = None

_OLD = (
    "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
    "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
    "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
    "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
    "'ftp_branch', 'ftp_nmd', 'ecl_exposure', 'crm_collateral', 'provision_held', 'cashflow')"
)
_NEW = _OLD[:-1] + ", 'credit_exposure')"
_TABLES = (
    ("bank_financial_facts", "ck_bank_financial_facts_fact_group"),
    ("current_financial_facts", "ck_current_financial_facts_fact_group"),
)


def _swap(expression: str) -> None:
    for table, constraint in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(constraint, type_="check")
            batch_op.create_check_constraint(constraint, expression)


def upgrade() -> None:
    _swap(_NEW)


def downgrade() -> None:
    # Refuse if derived exposures still exist; do not delete financial evidence.
    _swap(_OLD)
