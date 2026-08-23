"""Require explicit reporting currency for Temenos connections.

Revision ID: 202608220033
Revises: 202608220032

The prior database default silently labelled an incomplete connection as GHS.
New connections must carry their institution's reporting currency explicitly.
"""

import sqlalchemy as sa

from alembic import op

revision = "202608220033"
down_revision = "202608220032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "temenos_connections",
        "default_currency",
        existing_type=sa.String(length=3),
        existing_nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    op.alter_column(
        "temenos_connections",
        "default_currency",
        existing_type=sa.String(length=3),
        existing_nullable=False,
        server_default=sa.text("'GHS'"),
    )