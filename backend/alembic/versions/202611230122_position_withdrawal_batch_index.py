"""Index the position rows each governed withdrawal retired.

Revision ID: 202611230122
Revises: 202611230121

``withdrawal_impact._first_ingested_at`` finds the rows a withdrawal retired by
``withdrawn_by_batch_id`` on every run read and every filing act. With no index it
scanned the bank's whole position history (about 4 s per withdrawal on the pilot
book). The index is partial, so it holds only withdrawn rows, and it is built
CONCURRENTLY outside the migration transaction so ingestion is never blocked while
it builds.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202611230122"
down_revision = "202611230121"
branch_labels = None
depends_on = None

_INDEX = "ix_canonical_position_snapshots_withdrawal_batch"
_TABLE = "canonical_position_snapshots"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            _INDEX,
            _TABLE,
            ["organization_id", "withdrawn_by_batch_id"],
            postgresql_where=sa.text("withdrawn_by_batch_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(_INDEX, table_name=_TABLE, postgresql_concurrently=True, if_exists=True)
