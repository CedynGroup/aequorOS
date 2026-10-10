"""Retain source-key references until recovery backups expire.

Revision ID: 202610100089
Revises: 202610100088
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202610100089"
down_revision = "202610100088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retained_bank_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("key_id", sa.String(2048), nullable=False),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("owner_account", sa.String(12), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decrypt_until", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["bank_encryption_keys.bank_id", "bank_encryption_keys.organization_id"],
        ),
        sa.UniqueConstraint("bank_id", "provider", "key_id", name="uq_retained_bank_keys_identity"),
        sa.CheckConstraint("provider = 'aws_kms'", name="ck_retained_bank_keys_provider"),
    )
    op.create_index("ix_retained_bank_keys_key_id", "retained_bank_keys", ["key_id"])


def downgrade() -> None:
    op.drop_index("ix_retained_bank_keys_key_id", table_name="retained_bank_keys")
    op.drop_table("retained_bank_keys")
