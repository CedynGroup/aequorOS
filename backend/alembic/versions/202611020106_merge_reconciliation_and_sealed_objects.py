"""Merge reconciliation proposals and sealed-object migration heads.

Revision ID: 202611020106
Revises: 202611010001, 202611010105

Both independent parent migrations must run before the shared head.
This merge does not change the schema or either parent's recorded history.
"""

revision = "202611020106"
down_revision = ("202611010001", "202611010105")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
