"""Merge instrument observations with the current main migration history.

Both parent histories must run before the shared head. No schema operation is
needed here, and previously published migration revisions remain unchanged.
"""

from __future__ import annotations

revision = "202611030001"
down_revision = ("202610230001", "202611020107")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
