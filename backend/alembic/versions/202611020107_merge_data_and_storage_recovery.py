"""Merge DATA governance and storage-version recovery migration heads.

Revision ID: 202611020107
Revises: 202611010104, 202611020106

Both independent parent histories must run before the shared head.
This merge does not change either parent's schema or recorded history.
"""

revision = "202611020107"
down_revision = ("202611010104", "202611020106")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
