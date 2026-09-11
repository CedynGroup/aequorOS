"""Bind integration keys to one exact institution.

Revision ID: 202609110051
Revises: 202608290047

Existing keys deliberately remain NULL-scoped. No bank or machine authority is
inferred during migration; those credentials are denied until operators rotate
them through normal issuance.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609110051"
down_revision = "202608290047"
branch_labels = None
depends_on = None

_TABLE = "integration_keys"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("bank_id", sa.String(length=16), nullable=True))
    op.create_foreign_key(
        "fk_integration_keys_bank_tenant",
        _TABLE,
        "banks",
        ["bank_id", "organization_id"],
        ["id", "organization_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_integration_keys_organization_bank",
        _TABLE,
        ["organization_id", "bank_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    scoped_keys = connection.scalar(
        sa.text(f"SELECT count(*) FROM {_TABLE} WHERE bank_id IS NOT NULL")
    )
    if scoped_keys:
        raise RuntimeError(
            "Cannot safely downgrade bank-scoped integration keys after issuance; "
            "the downgrade would discard institution targets while older code "
            "accepts active keys organization-wide."
        )
    op.drop_index("ix_integration_keys_organization_bank", table_name=_TABLE)
    op.drop_constraint("fk_integration_keys_bank_tenant", _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, "bank_id")
