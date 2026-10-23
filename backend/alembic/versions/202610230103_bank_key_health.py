"""Persist successful bank key use and rotation timestamps.

Revision ID: 202610230103
Revises: 202610220102
"""

import sqlalchemy as sa

from alembic import op

revision = "202610230103"
down_revision = "202610220102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("last_successful_use_at", "last_rotated_at", "rotation_confirmed_at"):
        op.add_column("bank_encryption_keys", sa.Column(name, sa.DateTime(timezone=True)))

    op.add_column("bank_encryption_keys", sa.Column("rotation_period_days", sa.Integer()))


def downgrade() -> None:
    op.drop_column("bank_encryption_keys", "rotation_period_days")
    for name in ("rotation_confirmed_at", "last_rotated_at", "last_successful_use_at"):
        op.drop_column("bank_encryption_keys", name)
