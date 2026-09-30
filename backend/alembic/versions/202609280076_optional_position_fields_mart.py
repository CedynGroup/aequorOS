"""The four optional position fields reach the mart, and ``R12`` guards their completeness.

Revision ID: 202609280076
Revises: 202609280075

``docs/bi.md`` §Phase 5 asks for four optional Data Engine fields — ``officer_id``,
``channel``, ``account_status``, ``arrears_amount``. Ingestion needed no migration:
they ride the ``attributes`` bag and are normalised there. **Analysis does.** A BI
catalogue member binds to a ``ColumnRef(table, column)`` resolved against
``app/models/bi.py``, and the position facts carry no ``attributes`` column and
there is no key-value dimension table. ``attributes.branch_id`` is analysable today
precisely because the mart has a typed ``branch_code`` column the extract copies it
into — so the established pattern is a COLUMN, one layer past the bag.

Both position facts, or neither. They share ``_PositionFactColumns`` and
``test_extract_rows_produce_every_mart_column`` is parametrised over both and
asserts EQUALITY, so a column with no row field is caught as well as the reverse.

``ADD COLUMN`` on a range-partitioned parent propagates to every existing child and
to the DEFAULT partition on PostgreSQL 11+, so the monthly and yearly children need
no separate statement and **no RLS work**: a new column on an existing table
inherits that table's ENABLE+FORCE row-level security and its tenant policy. The
``SECURITY DEFINER`` partition functions exist to CREATE children; nothing here
creates one.

Widths are derived, never guessed. Audit A8-01 was a column four characters too
narrow for the values copied into it, which failed a tenant's whole nightly build
every night — and SQLite cannot see a VARCHAR length, so the hermetic suite agreed
with the wrong number. ``officer_id`` 120 is the width of ``branch_code``, and the
ingestion layer refuses anything longer, so the two cannot disagree. ``channel`` 32
and ``account_status`` 16 both exceed their vocabularies' longest value with room
to spare, and a parity test asserts that relationship rather than restating the
numbers.

No new index. The facts already carry ``(organization_id, bank_id, as_of_date,
position_type)`` and ``(…, branch_code)``; three more would be paid on every
nightly rebuild for every tenant. Measure with ``scripts/bi_benchmark.py`` first.

``arrears_amount_rc`` is NULLABLE and its CHECK admits NULL, because the two
reasons it can be absent are both real and neither is zero: the bank stated no
arrears, or the position's currency is not the reporting currency (the derivation
rule D-015, exactly as ``balance_rc`` behaves). A zero would assert a performing
facility.

``R12``, the arrears completeness check
---------------------------------------
Admitted here because a bank that supplies ``arrears_amount`` for HALF its book
would otherwise show a sum that reads as the whole book's arrears and a share that
is silently understated. That is the defect R10 (``dpd_completeness``) exists for:
a bank that never supplied days-past-due would read 0%, indistinguishable from a
clean book. R11 is taken by the GL-by-branch identity in ``202609280075``, so R12
is the next free id.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202609280076"
down_revision = "202609280075"
branch_labels = None
depends_on = None

_POSITION_FACTS = ("bi_fact_position_daily", "bi_fact_position_eom")

#: Widths mirrored from the ingestion vocabularies; see the module docstring.
#: ``TypeEngine[Any]`` rather than ``TypeEngine[object]``: the type parameter is
#: INVARIANT, so ``String`` (a ``TypeEngine[str]``) is not assignable to the
#: ``object`` form and the annotation would be a type error rather than a widening.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[Any]], ...] = (
    ("officer_id", sa.String(length=120)),
    ("channel", sa.String(length=32)),
    ("account_status", sa.String(length=16)),
    ("arrears_amount_rc", sa.Numeric(28, 6)),
)

_RECON_TABLE = "bi_reconciliation_results"
_RECON_CONSTRAINT = "ck_bi_reconciliation_results_check_id"
_CHECKS_BEFORE = "'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10', 'R11'"
_CHECKS_AFTER = f"{_CHECKS_BEFORE}, 'R12'"


def upgrade() -> None:
    for table in _POSITION_FACTS:
        for name, column_type in _NEW_COLUMNS:
            op.add_column(table, sa.Column(name, column_type, nullable=True))
        op.create_check_constraint(
            f"ck_{table}_arrears_amount_rc",
            table,
            "arrears_amount_rc IS NULL OR arrears_amount_rc >= 0",
        )

    with op.batch_alter_table(_RECON_TABLE) as batch:
        batch.drop_constraint(_RECON_CONSTRAINT, type_="check")
        batch.create_check_constraint(_RECON_CONSTRAINT, f"check_id IN ({_CHECKS_AFTER})")


def downgrade() -> None:
    # NARROWING moves data. The DELETE runs under ``force_rls_suspended`` because
    # alembic runs as the tenant-scoped app role and would otherwise match zero
    # rows in every tenant and report success, leaving exactly the rows that then
    # make the constraint fail to apply — the ``202608150013`` lesson.
    with force_rls_suspended(op.get_bind(), _RECON_TABLE):
        op.execute(f"DELETE FROM {_RECON_TABLE} WHERE check_id = 'R12'")
    with op.batch_alter_table(_RECON_TABLE) as batch:
        batch.drop_constraint(_RECON_CONSTRAINT, type_="check")
        batch.create_check_constraint(_RECON_CONSTRAINT, f"check_id IN ({_CHECKS_BEFORE})")

    for table in _POSITION_FACTS:
        op.drop_constraint(f"ck_{table}_arrears_amount_rc", table, type_="check")
        for name, _ in reversed(_NEW_COLUMNS):
            op.drop_column(table, name)
