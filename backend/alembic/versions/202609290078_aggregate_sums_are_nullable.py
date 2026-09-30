"""An aggregate cell's sums may be NULL, because absent is not zero.

Revision ID: 202609290078
Revises: 202609280077

Audit A360 finding H1. ``bi_agg_position_daily`` is the pre-aggregated source the
compiler swaps in for any measure that names NO dimension — which is every KPI
tile and every headline figure. Its seven ``*_sum`` columns were ``NOT NULL``, and
the builder initialised each accumulator to ``Decimal(0)``, so a grain cell whose
rows ALL carry NULL in a nullable money column was stored as ``0`` and served as
``0``. The fact source, asked the same question, answered NULL.

So the two paths disagreed exactly where the platform's structural rule says they
must not: **missing data is never zero**. A bank that states no collateral read
``Collateral value: 0.00`` on a board tile; a branch whose whole book is
unconverted foreign currency read ``GHS 0.00``. The zero then travelled — an
alert on a floor breached on a figure nobody had reported, target attainment
computed against a denominator of nothing, and a dashboard that treats ``0`` as a
measurement and so never shows its "needs data" state.

``SUM`` of nothing but NULLs is NULL in SQL. These columns now say so, and the
builder no longer substitutes a zero it was never given.

The two COUNTS stay ``NOT NULL`` on purpose: a cell exists only because rows do,
so its ``row_count`` is never absent. Absence is a property of a VALUE here, not
of the cell.

Why the downgrade backfills rather than refusing
------------------------------------------------
Going back to a schema that cannot express "absent" means the rows must stop
expressing it, so ``downgrade`` writes ``0`` where the value is NULL and restores
``NOT NULL``. That restores the pre-revision behaviour faithfully, including its
defect — which is what a downgrade IS.

This is deliberately NOT the shape ``202609280077`` uses. That revision guards an
append-only AUDIT tier, where discarding rows to fit a schema would destroy
evidence, so it refuses. These are derived mart rows: every one is rebuilt from
canonical data by the next build, nothing here is evidence of anything, and a
refusal would strand the whole release with no way back (audit A360 finding on
0077 makes exactly that point). A downgrade past this revision is always
available.

The backfill runs under ``force_rls_suspended``: alembic connects as the
application role, and ``bi_agg_position_daily`` is RLS-FORCED, so an UPDATE
without it silently touches zero rows across every tenant — the failure mode
recorded against migration ``202608150013``.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202609290078"
down_revision = "202609280077"
branch_labels = None
depends_on = None

TABLE = "bi_agg_position_daily"

#: Every summed money column on the aggregate. The two counts (``row_count``,
#: ``fx_unconverted_count``) are deliberately absent: see the module docstring.
SUM_COLUMNS: tuple[str, ...] = (
    "balance_rc_sum",
    "classification_exposure_rc_sum",
    "non_performing_exposure_rc_sum",
    "provision_required_rc_sum",
    "provision_held_rc_sum",
    "collateral_rc_sum",
    "rate_x_balance_rc_sum",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        # SQLite (the hermetic suite) builds from the models with `create_all`
        # and has no ALTER for this; the models already carry the nullability.
        return
    # On a partitioned parent, PostgreSQL propagates DROP NOT NULL to every
    # existing partition, including the DEFAULT one — the children inherit the
    # constraint rather than owning a copy of it.
    for column in SUM_COLUMNS:
        op.execute(sa.text(f'ALTER TABLE {TABLE} ALTER COLUMN {column} DROP NOT NULL'))


def downgrade() -> None:
    if not _is_postgres():
        return
    with force_rls_suspended(op.get_bind(), TABLE):
        for column in SUM_COLUMNS:
            op.execute(
                sa.text(f'UPDATE {TABLE} SET {column} = 0 WHERE {column} IS NULL')
            )
    for column in SUM_COLUMNS:
        op.execute(sa.text(f'ALTER TABLE {TABLE} ALTER COLUMN {column} SET NOT NULL'))
