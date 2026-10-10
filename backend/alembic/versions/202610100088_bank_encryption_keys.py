"""Bank-held master-key references and wrapped object keys.

Revision ID: 202610100088
Revises: 202610100087
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202610100088"
down_revision = "202610100087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Control-plane configuration, like tenant_storage: no financial values
    # or plaintext keys; storage resolves these records across tenants.
    op.create_table(
        "bank_encryption_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bank_id", sa.String(16), nullable=False, unique=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("storage_slug", sa.String(255), nullable=False, unique=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("key_id", sa.String(2048), nullable=False),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("owner_account", sa.String(12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]
        ),
        sa.UniqueConstraint("bank_id", "organization_id", name="uq_bank_encryption_keys_bank_org"),
        sa.CheckConstraint("provider = 'aws_kms'", name="ck_bank_encryption_keys_provider"),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'unavailable')", name="ck_bank_encryption_keys_status"
        ),
    )
    op.create_table(
        "object_key_envelopes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("context_digest", sa.String(64), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["bank_encryption_keys.bank_id", "bank_encryption_keys.organization_id"],
        ),
    )
    op.create_index("ix_object_key_envelopes_bank_id", "object_key_envelopes", ["bank_id"])

    op.execute("ALTER TABLE object_key_envelopes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE object_key_envelopes FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY object_key_envelopes_tenant_isolation ON object_key_envelopes
        USING (organization_id = nullif(current_setting('app.organization_id', true), ''))
        WITH CHECK (organization_id = nullif(current_setting('app.organization_id', true), ''))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY object_key_envelopes_tenant_isolation ON object_key_envelopes")
    op.execute("ALTER TABLE object_key_envelopes DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_object_key_envelopes_bank_id", table_name="object_key_envelopes")
    op.drop_table("object_key_envelopes")
    op.drop_table("bank_encryption_keys")
