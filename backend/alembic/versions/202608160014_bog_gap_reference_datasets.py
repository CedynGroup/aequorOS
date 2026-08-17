"""BoG return data-gap reference datasets: widen the dataset_kind CHECK

Revision ID: 202608160014
Revises: 202608150013

The official BSD returns (bog_forms/) need lines no canonical entity carried
(bank CoA → BSD7A P&L items, subsidiaries, tariffs, capex, ATM operations,
inward remittances, teller withdrawals, accrued-interest sub-ledger). Each is
a REFERENCE DATASET — uploaded through the app or pushed through the API like
every other dataset (no seeding), rows preserved verbatim under the kind on
``canonical_reference_rows`` with full batch lineage. This migration admits the
eight new kinds on ``ck_canonical_reference_rows_dataset_kind``; per-kind
schemas are documented in docs/data_engine/datasets/<kind>.md.
"""

from __future__ import annotations

from alembic import op

revision = "202608160014"
down_revision = "202608150013"
branch_labels = None
depends_on = None

TABLE = "canonical_reference_rows"
CONSTRAINT = "ck_canonical_reference_rows_dataset_kind"

KINDS_BEFORE = (
    "'capital_structure', 'behavioral_assumptions', 'yield_curve', 'fx_rates_current', "
    "'fx_rates_historical', 'historical_cashflows', 'historical_financials', "
    "'business_units', 'institution'"
)
KINDS_AFTER = (
    KINDS_BEFORE + ", 'gl_mapping_bsd7', 'subsidiaries', 'tariff_schedule', "
    "'capital_expenditure', 'atm_operations', 'remittance_flows', 'teller_withdrawals', "
    "'interest_accruals'"
)


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, f"dataset_kind IN ({KINDS_AFTER})")


def downgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, f"dataset_kind IN ({KINDS_BEFORE})")
