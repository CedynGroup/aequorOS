"""Index measured reverse-lineage checks on the two large canonical books.

The parent id is globally unique; a single-column path bounds candidates while
PostgreSQL still checks organization and bank for the complete scoped FK. Keep
history, low-volume canonical tables and current-generation predicates unchanged.
Evidence and build-lock tradeoffs: backend/docs/database_integrity.md.
"""

from __future__ import annotations

from alembic import op

revision = "202610140100"
down_revision = "202610120099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_canonical_positions_lineage_id", "canonical_positions", ["lineage_id"])
    op.create_index(
        "ix_canonical_position_snapshots_lineage_id", "canonical_position_snapshots", ["lineage_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_position_snapshots_lineage_id", table_name="canonical_position_snapshots"
    )
    op.drop_index("ix_canonical_positions_lineage_id", table_name="canonical_positions")
