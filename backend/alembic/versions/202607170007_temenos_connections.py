"""temenos connections

Revision ID: 202607170007
Revises: 202607170006
Create Date: 2026-07-17 23:00:00.000000

Temenos T24 core-banking adapter operational table (docs/temenos_adapter.md):
one configured connection per bank carrying transport mode, endpoint, enabled
domains, pull schedule, per-bank catalog overrides, and the encrypted service
credential (credential lifecycle mirrored from the market-data connection).

Additive-only: creates ``temenos_connections`` with the composite-FK to banks,
CHECK constraints on core_system / connection_mode / status, uniqueness on
(id, org) and (org, bank, display_name), and the org/bank index, then ENABLE +
FORCE row-level security with the tenant-isolation policy. ``downgrade`` drops
the policy, RLS, and the table.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607170007"
down_revision = "202607170006"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"

CORE_SYSTEMS = "'T24', 'FINACLE', 'FLEXCUBE'"
CONNECTION_MODES = "'OFS', 'IRIS', 'OPEN_API'"
CONNECTION_STATUSES = (
    "'TESTING', 'ACTIVE', 'EXPIRING_SOON', 'EXPIRED', 'REVOKED', 'INVALID', "
    "'REPLACED_PENDING_DELETION', 'DISABLED'"
)

TABLE = "temenos_connections"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "core_system", sa.String(length=20), server_default=sa.text("'T24'"), nullable=False
        ),
        sa.Column("connection_mode", sa.String(length=20), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=30), server_default=sa.text("'TESTING'"), nullable=False
        ),
        sa.Column("credential_ciphertext", sa.Text(), nullable=True),
        sa.Column("credential_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("vault_path", sa.String(length=255), nullable=False),
        sa.Column("companies", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "default_currency", sa.String(length=3), server_default=sa.text("'GHS'"), nullable=False
        ),
        sa.Column("domains", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("schedule", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("catalog_overrides", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pull_status", sa.String(length=20), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"core_system IN ({CORE_SYSTEMS})", name="ck_temenos_connections_core_system"
        ),
        sa.CheckConstraint(
            f"connection_mode IN ({CONNECTION_MODES})", name="ck_temenos_connections_mode"
        ),
        sa.CheckConstraint(
            f"status IN ({CONNECTION_STATUSES})", name="ck_temenos_connections_status"
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_temenos_connections_id_org"),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "display_name",
            name="uq_temenos_connections_scope_name",
        ),
    )
    op.create_index(
        "ix_temenos_connections_org_bank",
        TABLE,
        ["organization_id", "bank_id"],
    )

    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {TABLE}_tenant_isolation
        ON {TABLE}
        USING (organization_id = {TENANT_ID_EXPR})
        WITH CHECK (organization_id = {TENANT_ID_EXPR})
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_temenos_connections_org_bank", table_name=TABLE)
    op.drop_table(TABLE)
