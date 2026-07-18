"""database direct connections

Revision ID: 202607170010
Revises: 202607170009
Create Date: 2026-07-17 23:30:00.000000

Database-Direct core adapter operational table: one configured direct
core-database connection per bank (Oracle / SQL Server / JDBC / ODBC),
carrying endpoints, TLS and read-replica policy, the per-bank ExtractionSpec,
and the sealed read-only service credential (lifecycle mirrored from the
market-data / Temenos connection). Mirrors ``temenos_connections``: composite
FK to banks, CHECK constraints on backend / status, uniqueness on (id, org)
and (org, bank, display_name), the org/bank index, then ENABLE + FORCE
row-level security with the tenant-isolation policy.

Also, additively: adds the nullable JSON ``etl_report`` column to
``ingestion_batches`` (the compact ML-ETL preprocess/dedup summary), and widens
the ``ck_lineage_records_operation_type`` CHECK to admit the two new ML-ETL
lineage operation types (``ML_ETL_PREPROCESS`` and ``ML_ETL_DEDUP``).

``downgrade`` drops the policy, RLS, and the connection table, drops the
``etl_report`` column, and restores the narrower lineage-operation CHECK (after
removing any lineage rows carrying the two ML-ETL operation types so the tighter
constraint can be re-applied).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607170010"
down_revision = "202607170009"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"

BACKENDS = "'oracle', 'sqlserver', 'jdbc', 'odbc'"
CONNECTION_STATUSES = (
    "'TESTING', 'ACTIVE', 'EXPIRING_SOON', 'EXPIRED', 'REVOKED', 'INVALID', "
    "'REPLACED_PENDING_DELETION', 'DISABLED'"
)

TABLE = "database_direct_connections"

LINEAGE_TABLE = "lineage_records"
LINEAGE_OPERATION_CHECK = "ck_lineage_records_operation_type"
LINEAGE_OPERATION_TYPES_OLD = (
    "'ADAPTER_EXTRACT', 'ADAPTER_TRANSLATE', 'VALIDATION', 'ENRICHMENT', "
    "'ML_ENRICHMENT', 'HUMAN_OVERRIDE', 'MANUAL_ENTRY', 'SUPERSESSION'"
)
LINEAGE_OPERATION_TYPES_NEW = (
    "'ADAPTER_EXTRACT', 'ML_ETL_PREPROCESS', 'ML_ETL_DEDUP', 'ADAPTER_TRANSLATE', "
    "'VALIDATION', 'ENRICHMENT', 'ML_ENRICHMENT', 'HUMAN_OVERRIDE', 'MANUAL_ENTRY', "
    "'SUPERSESSION'"
)
NEW_LINEAGE_OPERATION_TYPES = "'ML_ETL_PREPROCESS', 'ML_ETL_DEDUP'"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("backend", sa.String(length=20), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "status", sa.String(length=30), server_default=sa.text("'TESTING'"), nullable=False
        ),
        sa.Column("host", sa.String(length=255), server_default=sa.text("''"), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("database", sa.String(length=255), server_default=sa.text("''"), nullable=False),
        sa.Column("service_name", sa.String(length=255), nullable=True),
        sa.Column("schemas", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("read_replicas", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "prefer_read_replica",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "tls_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "tls_verify_server_certificate",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "query_timeout_seconds",
            sa.Integer(),
            server_default=sa.text("300"),
            nullable=False,
        ),
        sa.Column(
            "connection_options", sa.JSON(), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("extraction_spec", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("credential_ciphertext", sa.Text(), nullable=True),
        sa.Column("credential_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("vault_path", sa.String(length=255), nullable=False),
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        # Wide enough for the longest ingestion outcome ("accepted_with_warnings").
        sa.Column("last_sync_status", sa.String(length=40), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"backend IN ({BACKENDS})", name="ck_database_direct_connections_backend"
        ),
        sa.CheckConstraint(
            f"status IN ({CONNECTION_STATUSES})", name="ck_database_direct_connections_status"
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_database_direct_connections_id_org"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "display_name",
            name="uq_database_direct_connections_scope_name",
        ),
    )
    op.create_index(
        "ix_database_direct_connections_org_bank",
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

    op.add_column(
        "ingestion_batches",
        sa.Column("etl_report", sa.JSON(), nullable=True),
    )

    with op.batch_alter_table(LINEAGE_TABLE) as batch_op:
        batch_op.drop_constraint(LINEAGE_OPERATION_CHECK, type_="check")
        batch_op.create_check_constraint(
            LINEAGE_OPERATION_CHECK,
            f"operation_type IN ({LINEAGE_OPERATION_TYPES_NEW})",
        )


def downgrade() -> None:
    # Remove lineage rows carrying the two new ML-ETL operation types before
    # restoring the tighter CHECK that no longer admits them.
    op.execute(
        f"DELETE FROM {LINEAGE_TABLE} WHERE operation_type IN ({NEW_LINEAGE_OPERATION_TYPES})"
    )
    with op.batch_alter_table(LINEAGE_TABLE) as batch_op:
        batch_op.drop_constraint(LINEAGE_OPERATION_CHECK, type_="check")
        batch_op.create_check_constraint(
            LINEAGE_OPERATION_CHECK,
            f"operation_type IN ({LINEAGE_OPERATION_TYPES_OLD})",
        )

    op.drop_column("ingestion_batches", "etl_report")

    op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_database_direct_connections_org_bank", table_name=TABLE)
    op.drop_table(TABLE)
