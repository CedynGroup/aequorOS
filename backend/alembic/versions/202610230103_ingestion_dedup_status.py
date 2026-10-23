"""Keep ingestion deduplication lifecycle metadata outside protected reports.

Revision ID: 202610230103
Revises: 202610220102
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202610230103"
down_revision = "202610220102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_batches", sa.Column("dedup_status", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("ingestion_batches", "dedup_status")
