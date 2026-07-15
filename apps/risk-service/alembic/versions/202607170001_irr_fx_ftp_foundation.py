"""irr fx ftp foundation

Revision ID: 202607170001
Revises: 202607160003
Create Date: 2026-07-17 09:00:00.000000

Shared widening that the IRRBB, FX, and FTP verticals all build on. No new
tables: this migration only widens four ``CHECK`` constraints and adds one
metric unit so the existing regulatory-run infrastructure can persist interest
-rate-risk, foreign-exchange, and funds-transfer-pricing runs, facts, line
items, stress parameters, and duration metrics. The row-level-security policies
on the altered tables are untouched (they key off ``organization_id``, which is
unchanged). ``downgrade`` deletes any rows using the new values before
restoring the narrower constraints so the reversal never violates them.
"""

from alembic import op

revision = "202607170001"
down_revision = "202607160003"
branch_labels = None
depends_on = None

# regulatory_runs.module
RUNS_TABLE = "regulatory_runs"
RUNS_CONSTRAINT = "ck_regulatory_runs_module"
RUNS_ORIGINAL = "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif')"
RUNS_WIDENED = (
    "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif', 'irr', 'fx', 'ftp')"
)

# bank_financial_facts.fact_group
FACTS_TABLE = "bank_financial_facts"
FACTS_CONSTRAINT = "ck_bank_financial_facts_fact_group"
FACTS_ORIGINAL = (
    "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
    "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
    "'deposit_behavior')"
)
FACTS_WIDENED = (
    "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
    "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
    "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
    "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
    "'ftp_branch', 'ftp_nmd')"
)
FACTS_NEW_GROUPS = (
    "'irr_position', 'irr_swap', 'fx_position', 'fx_return_history', 'fx_hedge', "
    "'ftp_curve_point', 'ftp_product', 'ftp_branch', 'ftp_nmd'"
)

# regulatory_line_items.section
LINES_TABLE = "regulatory_line_items"
LINES_CONSTRAINT = "ck_regulatory_line_items_section"
LINES_ORIGINAL = (
    "section IN ('hqla', 'outflow', 'inflow', 'asf', 'rsf', 'credit_rwa', "
    "'market_rwa', 'operational_rwa', 'capital_component', 'ratio')"
)
LINES_WIDENED = (
    "section IN ('hqla', 'outflow', 'inflow', 'asf', 'rsf', 'credit_rwa', "
    "'market_rwa', 'operational_rwa', 'capital_component', 'ratio', "
    "'irr_gap', 'irr_eve', 'irr_ear', 'fx_position', 'fx_var', 'fx_hedge', "
    "'ftp_curve', 'ftp_product', 'ftp_branch')"
)
LINES_NEW_SECTIONS = (
    "'irr_gap', 'irr_eve', 'irr_ear', 'fx_position', 'fx_var', 'fx_hedge', "
    "'ftp_curve', 'ftp_product', 'ftp_branch'"
)

# param_stress_shock.module
SHOCK_TABLE = "param_stress_shock"
SHOCK_CONSTRAINT = "ck_param_stress_shock_module"
SHOCK_ORIGINAL = "module IN ('liquidity', 'capital', 'forecast')"
SHOCK_WIDENED = "module IN ('liquidity', 'capital', 'forecast', 'irr', 'fx', 'ftp')"

# regulatory_metric_results.unit
UNIT_TABLE = "regulatory_metric_results"
UNIT_CONSTRAINT = "ck_regulatory_metric_results_unit"
UNIT_ORIGINAL = "unit IN ('pct', 'ghs')"
UNIT_WIDENED = "unit IN ('pct', 'ghs', 'years')"


def _swap_check(table: str, constraint: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_constraint(constraint, type_="check")
        batch_op.create_check_constraint(constraint, expression)


def upgrade() -> None:
    _swap_check(RUNS_TABLE, RUNS_CONSTRAINT, RUNS_WIDENED)
    _swap_check(FACTS_TABLE, FACTS_CONSTRAINT, FACTS_WIDENED)
    _swap_check(LINES_TABLE, LINES_CONSTRAINT, LINES_WIDENED)
    _swap_check(SHOCK_TABLE, SHOCK_CONSTRAINT, SHOCK_WIDENED)
    _swap_check(UNIT_TABLE, UNIT_CONSTRAINT, UNIT_WIDENED)


def downgrade() -> None:
    # Delete rows using the widened values before narrowing the constraints so
    # the restored CHECKs are never violated by pre-existing data.
    op.execute(f"DELETE FROM {RUNS_TABLE} WHERE module IN ('irr', 'fx', 'ftp')")
    op.execute(f"DELETE FROM {FACTS_TABLE} WHERE fact_group IN ({FACTS_NEW_GROUPS})")
    op.execute(f"DELETE FROM {LINES_TABLE} WHERE section IN ({LINES_NEW_SECTIONS})")
    op.execute(f"DELETE FROM {SHOCK_TABLE} WHERE module IN ('irr', 'fx', 'ftp')")
    op.execute(f"DELETE FROM {UNIT_TABLE} WHERE unit = 'years'")

    _swap_check(UNIT_TABLE, UNIT_CONSTRAINT, UNIT_ORIGINAL)
    _swap_check(SHOCK_TABLE, SHOCK_CONSTRAINT, SHOCK_ORIGINAL)
    _swap_check(LINES_TABLE, LINES_CONSTRAINT, LINES_ORIGINAL)
    _swap_check(FACTS_TABLE, FACTS_CONSTRAINT, FACTS_ORIGINAL)
    _swap_check(RUNS_TABLE, RUNS_CONSTRAINT, RUNS_ORIGINAL)
