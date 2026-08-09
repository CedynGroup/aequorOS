"""regulatory_runs.module admits 'reverse_stress'

Revision ID: 202608070034
Revises: 202608070033

Phase 2 item 4: the reverse-stress frontier persists as an immutable
RegulatoryRun so it carries the same input-hash provenance as every other
official run. The module CHECK enumerated the eight engine modules; this
adds the ninth. batch_alter_table because the hermetic test database is
SQLite (same shape as 202607260030).
"""

from __future__ import annotations

from alembic import op

revision = "202608070034"
down_revision = "202608070033"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_regulatory_runs_module"
_BEFORE = (
    "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif', 'irr', 'fx', 'ftp')"
)
_AFTER = (
    "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif', 'irr', 'fx', "
    "'ftp', 'reverse_stress')"
)


def upgrade() -> None:
    with op.batch_alter_table("regulatory_runs") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _AFTER)


def downgrade() -> None:
    # Refuses (via constraint creation failure) if reverse-stress runs exist —
    # deleting immutable runs is an operator decision, never a migration's.
    with op.batch_alter_table("regulatory_runs") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _BEFORE)
