"""storage artifacts

Revision ID: 202607160003
Revises: 202607160002
Create Date: 2026-07-16 21:00:00.000000

Wires the Data Engine to the storage layer (storage.md §1.3): banks get a
DNS-safe storage slug used in bucket names, and ingestion batches record
where their raw source file and validation report landed.
"""

import sqlalchemy as sa

from alembic import op

revision = "202607160003"
down_revision = "202607160002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("banks", sa.Column("storage_slug", sa.String(length=63), nullable=True))
    op.create_index(
        "uq_banks_storage_slug",
        "banks",
        ["storage_slug"],
        unique=True,
        postgresql_where=sa.text("storage_slug IS NOT NULL"),
    )
    op.add_column(
        "ingestion_batches",
        sa.Column("raw_artifact_path", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "ingestion_batches",
        sa.Column("report_artifact_path", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingestion_batches", "report_artifact_path")
    op.drop_column("ingestion_batches", "raw_artifact_path")
    op.drop_index("uq_banks_storage_slug", table_name="banks")
    op.drop_column("banks", "storage_slug")
