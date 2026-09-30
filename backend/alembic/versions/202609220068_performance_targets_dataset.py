"""Admit the ``performance_targets`` reference dataset kind.

Revision ID: 202609220068
Revises: 202609220067

BI Phase 2 compares every targetable measure against the bank's own budget and
reforecast. Those targets are a REFERENCE DATASET like every other bank-supplied
register — uploaded or pushed, rows preserved verbatim on
``canonical_reference_rows`` with full batch lineage, never seeded and never
derived from a filed return — so the only schema change the feature needs is the
kind's admission to ``ck_canonical_reference_rows_dataset_kind``. What a
well-formed row looks like lives in
``app/domain/ingestion/reference_schemas/performance_targets.py``.

``business_units`` — the sibling Phase 2 schema, which makes the BI branch
region real — needs NO migration: it has been an admitted kind since the
constraint was first written, so re-adding it would be a no-op that the
downgrade would then have to un-say.

Drop-and-create as ``202608160014`` did, with the BEFORE list written out rather
than derived from ``REFERENCE_DATASET_KINDS``: a literal keeps a fresh upgrade
and an already-migrated database on the same constraint after the next kind is
added, and makes the downgrade restore the historical shape. The parity between
the constant and the migrated CHECK is asserted instead
(``tests/db/test_performance_targets_migration.py``), so a kind added to the
tuple without a migration fails there rather than in a push.

Widening moves no data. NARROWING does: a ``performance_targets`` row cannot
satisfy the older constraint, so ``downgrade()`` deletes those rows first — see
the note there.
"""

from __future__ import annotations

from alembic import op
from app.db.session import force_rls_suspended

revision = "202609220068"
down_revision = "202609220067"
branch_labels = None
depends_on = None

TABLE = "canonical_reference_rows"
CONSTRAINT = "ck_canonical_reference_rows_dataset_kind"
NEW_KIND = "performance_targets"

KINDS_BEFORE = (
    "'capital_structure', 'behavioral_assumptions', 'yield_curve', 'fx_rates_current', "
    "'fx_rates_historical', 'historical_cashflows', 'historical_financials', "
    "'business_units', 'institution', 'gl_mapping_bsd7', 'subsidiaries', "
    "'tariff_schedule', 'capital_expenditure', 'atm_operations', 'remittance_flows', "
    "'teller_withdrawals', 'interest_accruals'"
)
KINDS_AFTER = f"{KINDS_BEFORE}, '{NEW_KIND}'"


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, f"dataset_kind IN ({KINDS_AFTER})")


def downgrade() -> None:
    # The target rows have to go before the narrowed constraint is recreated:
    # a stored `performance_targets` row cannot satisfy it, and the CHECK
    # applies to every row, not only to new ones. A downgrade past this
    # revision therefore discards whatever budgets a tenant had pushed; it is
    # not a routine operation.
    #
    # `canonical_reference_rows` is FORCE-RLS and alembic runs as the
    # tenant-scoped app role, so this DELETE would otherwise match zero rows in
    # every tenant and report success — leaving exactly the rows behind that
    # then make the constraint fail to apply. `force_rls_suspended` lifts FORCE
    # for the owner inside this transaction only, and is loud when the role
    # cannot lift it.
    with force_rls_suspended(op.get_bind(), TABLE):
        op.execute(f"DELETE FROM {TABLE} WHERE dataset_kind = '{NEW_KIND}'")
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, f"dataset_kind IN ({KINDS_BEFORE})")
