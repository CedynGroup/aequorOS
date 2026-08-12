"""Desk determinations: research_adjustments JSON for Track-1 weekly judgment.

Option B product decision: each determination may carry analyst overrides,
additive bps spreads, and assumption notes without rewriting the methodology
register. Column defaults to empty list; writable only while draft.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import text as sql_text

from alembic import op

revision = "202608110046"
down_revision = "202608110045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "desk_determinations",
        sa.Column(
            "research_adjustments",
            sa.JSON(),
            nullable=False,
            server_default=sql_text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("desk_determinations", "research_adjustments")
