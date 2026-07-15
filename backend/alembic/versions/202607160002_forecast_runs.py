"""forecast runs

Revision ID: 202607160002
Revises: 202607160001
Create Date: 2026-07-16 09:00:00.000000

Widens the ``regulatory_runs.module`` check constraint so the balance-sheet
forecasting vertical can persist ``forecast``, ``optimizer``, and ``whatif``
runs alongside the existing liquidity and capital modules. No new tables; the
row-level-security policy on ``regulatory_runs`` is untouched.
"""

from alembic import op

revision = "202607160002"
down_revision = "202607160001"
branch_labels = None
depends_on = None

TABLE = "regulatory_runs"
CONSTRAINT = "ck_regulatory_runs_module"
ORIGINAL_MODULES = "module IN ('liquidity', 'capital')"
WIDENED_MODULES = "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif')"


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.drop_constraint(CONSTRAINT, type_="check")
        batch_op.create_check_constraint(CONSTRAINT, WIDENED_MODULES)


def downgrade() -> None:
    op.execute(f"DELETE FROM {TABLE} WHERE module IN ('forecast', 'optimizer', 'whatif')")
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.drop_constraint(CONSTRAINT, type_="check")
        batch_op.create_check_constraint(CONSTRAINT, ORIGINAL_MODULES)
