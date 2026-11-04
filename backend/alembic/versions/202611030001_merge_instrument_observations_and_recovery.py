"""Merge instrument observations and storage-recovery migration heads.

Revision ID: 202611030001
Revises: 202610230001, 202611020107

The regulatory status observations history and the DATA/storage recovery
history branched independently and must both run before the shared head.
This merge does not change either parent's schema or recorded history.
"""

revision = "202611030001"
down_revision = ("202610230001", "202611020107")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
