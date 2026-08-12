"""Market-data entitlement tiers (spec §10 dataset permissioning)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608110047"
down_revision = "202608110046"
branch_labels = None
depends_on = None

_TABLE = "market_data_entitlements"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(length=20), nullable=False),
        sa.Column("dataset_code", sa.String(length=40), nullable=False),
        sa.Column("tier", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="active"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("granted_by", sa.String(length=320), nullable=False),
        sa.Column("revoked_by", sa.String(length=320), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_market_data_entitlements_status",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_market_data_entitlements_window",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "dataset_code",
            "effective_from",
            name="uq_market_data_entitlements_org_dataset_from",
        ),
    )
    op.create_index(
        "ix_market_data_entitlements_org_dataset",
        _TABLE,
        ["organization_id", "dataset_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_data_entitlements_org_dataset", table_name=_TABLE)
    op.drop_table(_TABLE)
