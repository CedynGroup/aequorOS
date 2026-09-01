"""Admit the ``provision_held`` fact group on both fact planes.

Provisions the bank HOLDS against its loan book were never a derived fact:
``ecl_provision_ghs`` reached BSD8 and the Large Exposures return straight off
the position attribute, but no fact group carried it — so provision coverage
(held ÷ NPL exposure) was structurally uncomputable. ``report_comparison``
declares ``provision_coverage_pct``/``npl_coverage_pct`` with no producer, the
SDI scorecard reports its coverage evidence as unavailable, and the bank
scorecard substitutes the entity-level ``general_provisions`` capital component
as a numerator. ``fact_derivation._derive_provision_held`` now emits
``provision_held`` (categories ``specific`` / ``general`` /
``interest_in_suspense``) on both the official and the live plane, and this
migration widens the two fact-group CHECK constraints to admit it.

Constraint rewrite only. No row is read or written: a book whose loans state no
provision attribute derives nothing (the group reports ``skipped``), so
existing tenants gain rows only when their next derivation runs over a book
that states provisions.

Revision ID: 202609010044
Revises: 202608230043
"""

from __future__ import annotations

from alembic import op

revision = "202609010044"
down_revision = "202608280046"
branch_labels = None
depends_on = None

_COMMON = (
    "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
    "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
    "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
    "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
    "'ftp_branch', 'ftp_nmd', 'ecl_exposure', 'crm_collateral'"
)
_OLD = _COMMON + ", 'cashflow')"
_NEW = _COMMON + ", 'provision_held', 'cashflow')"

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
    _swap(_OLD)
