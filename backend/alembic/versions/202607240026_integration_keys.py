"""integration keys: revocable service-account API credentials

Revision ID: 202607240026
Revises: 202607240025

Bank middleware authenticates with a generate-once, revocable API key bound
to a per-key service account (users.auth_provider = 'service'). Only the
SHA-256 hash is stored. The table is DELIBERATELY not RLS-forced: the auth
path resolves the key hash before any tenant context exists (it carries no
secret material, and every endpoint filters by organization explicitly).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607240026"
down_revision = "202607240025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("service_user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("key_prefix", sa.String(length=20), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["service_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_integration_keys_key_hash", "integration_keys", ["key_hash"], unique=True
    )
    op.create_index(
        "ix_integration_keys_organization_id", "integration_keys", ["organization_id"]
    )
    # Service accounts join the allowed auth providers.
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_auth_provider")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_auth_provider "
        "CHECK (auth_provider IN ('password', 'oidc', 'service'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_auth_provider")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_auth_provider "
        "CHECK (auth_provider IN ('password', 'oidc'))"
    )
    op.drop_index("ix_integration_keys_organization_id", table_name="integration_keys")
    op.drop_index("uq_integration_keys_key_hash", table_name="integration_keys")
    op.drop_table("integration_keys")
