"""regulatory_runs.module admits 'enterprise_stress'

Revision ID: 202608190022
Revises: 202608190021

Phase 2 (docs/stress.md §3.3–3.4): the enterprise-wide stress test persists as an
immutable RegulatoryRun (module enterprise_stress) carrying the value-based
input-hash provenance of every other official run, with the base+stress 3-year
projection and the Appendix II Tables 1–6 in ``metrics``. The module CHECK
enumerated nine engine modules; this adds the tenth. batch_alter_table because
the hermetic test database is SQLite (same shape as 202608070034).

The chosen down_revision is the concurrent live-state head 202608190021 — verified
as the single alembic head at authoring time — so this migration keeps the chain
linear rather than forking a second head off the Phase 1 macro-scenario spine.
"""

from __future__ import annotations

from alembic import op

revision = "202608190022"
down_revision = "202608190021"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_regulatory_runs_module"
_BEFORE = (
    "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif', 'irr', 'fx', "
    "'ftp', 'reverse_stress')"
)
_AFTER = (
    "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif', 'irr', 'fx', "
    "'ftp', 'reverse_stress', 'enterprise_stress')"
)


def upgrade() -> None:
    with op.batch_alter_table("regulatory_runs") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _AFTER)


def downgrade() -> None:
    # Refuses (via constraint creation failure) if enterprise-stress runs exist —
    # deleting immutable runs is an operator decision, never a migration's.
    with op.batch_alter_table("regulatory_runs") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _BEFORE)
