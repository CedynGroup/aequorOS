"""Keep ingestion deduplication lifecycle metadata outside protected reports.

Revision ID: 202610270001
Revises: 202610230103
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610270001"
down_revision = "202610230103"
branch_labels = None
depends_on = None


def backfill_status(connection: Connection) -> None:
    batches = sa.Table("ingestion_batches", sa.MetaData(), autoload_with=connection)
    status = batches.c.etl_report["dedup_status"].as_string()
    with force_rls_suspended(connection, "ingestion_batches"):
        _ = connection.execute(
            batches.update()
            .where(status.in_(("failed", "deferred", "completed")))
            .values(dedup_status=status)
        )


def upgrade() -> None:
    op.add_column("ingestion_batches", sa.Column("dedup_status", sa.String(20), nullable=True))
    backfill_status(op.get_bind())


def downgrade() -> None:
    op.drop_column("ingestion_batches", "dedup_status")
