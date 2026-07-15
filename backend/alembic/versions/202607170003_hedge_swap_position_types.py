"""hedge swap position types

Revision ID: 202607170003
Revises: 202607170002
Create Date: 2026-07-17 15:00:00.000000

Widens the canonical position-type ``CHECK`` so uploaded FX hedge books and
interest-rate swaps land as first-class canonical positions (``FX_HEDGE``,
``INTEREST_RATE_SWAP``). Only ``canonical_positions`` carries the position-type
constraint — snapshots reference positions by id and have no equivalent CHECK —
so this migration touches exactly one constraint. Instrument specifics (pair,
contract rate, MtM, effectiveness measures, swap legs) ride in the snapshot
``attributes`` JSON; no new columns are needed. The row-level-security policy
on the table is untouched (it keys off ``organization_id``, which is
unchanged). ``downgrade`` deletes rows of the new types (snapshots first, then
identities) before restoring the narrower constraint so the reversal never
violates it.
"""

from alembic import op

revision = "202607170003"
down_revision = "202607170002"
branch_labels = None
depends_on = None

TABLE = "canonical_positions"
CONSTRAINT = "ck_canonical_positions_position_type"
ORIGINAL = (
    "position_type IN ('LOAN', 'DEPOSIT', 'SECURITY_HOLDING', 'DERIVATIVE', 'CASH', "
    "'INTERBANK_PLACEMENT', 'INTERBANK_BORROWING', 'LC_GUARANTEE', "
    "'COMMITMENT_UNDRAWN', 'OTHER_ASSET', 'OTHER_LIABILITY')"
)
WIDENED = (
    "position_type IN ('LOAN', 'DEPOSIT', 'SECURITY_HOLDING', 'DERIVATIVE', 'FX_HEDGE', "
    "'INTEREST_RATE_SWAP', 'CASH', 'INTERBANK_PLACEMENT', 'INTERBANK_BORROWING', "
    "'LC_GUARANTEE', 'COMMITMENT_UNDRAWN', 'OTHER_ASSET', 'OTHER_LIABILITY')"
)
NEW_TYPES = "'FX_HEDGE', 'INTEREST_RATE_SWAP'"


def _swap_check(table: str, constraint: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_constraint(constraint, type_="check")
        batch_op.create_check_constraint(constraint, expression)


def upgrade() -> None:
    _swap_check(TABLE, CONSTRAINT, WIDENED)


def downgrade() -> None:
    # Snapshots reference the position identities, so they go first; then the
    # identities of the widened types, then the narrower constraint is safe.
    op.execute(
        "DELETE FROM canonical_position_snapshots WHERE position_id IN "
        f"(SELECT id FROM {TABLE} WHERE position_type IN ({NEW_TYPES}))"
    )
    op.execute(f"DELETE FROM {TABLE} WHERE position_type IN ({NEW_TYPES})")
    _swap_check(TABLE, CONSTRAINT, ORIGINAL)
