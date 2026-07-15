"""data engine canonical spine

Revision ID: 202607160001
Revises: 202607150002
Create Date: 2026-07-16 12:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607160001"
down_revision = "202607150002"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"

SOURCE_SYSTEMS = (
    "'EXCEL_CSV', 'T24', 'FINACLE', 'FLEXCUBE', 'DB_DIRECT', 'SFTP_DROP', 'API_GENERIC', 'MANUAL'"
)
VALIDATION_STATUSES = "'pending', 'accepted', 'warning', 'error', 'blocked'"

TABLES = (
    "mapping_configs",
    "ingestion_batches",
    "lineage_records",
    "translation_failures",
    "canonical_gl_accounts",
    "canonical_counterparties",
    "canonical_products",
    "canonical_positions",
    "canonical_position_snapshots",
)


def _canonical_metadata_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=False),
        sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("validation_status", sa.String(length=16), nullable=False),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _canonical_shared_constraints(table_name: str) -> list[sa.schema.SchemaItem]:
    return [
        sa.CheckConstraint(
            f"validation_status IN ({VALIDATION_STATUSES})",
            name=f"ck_{table_name}_validation_status",
        ),
        sa.CheckConstraint(
            f"source_system IN ({SOURCE_SYSTEMS})",
            name=f"ck_{table_name}_source_system",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id", "organization_id"],
            ["ingestion_batches.id", "ingestion_batches.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["lineage_id", "organization_id"],
            ["lineage_records.id", "lineage_records.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name=f"uq_{table_name}_id_org"),
    ]


def _canonical_shared_indexes(table_name: str) -> None:
    op.create_index(
        f"ix_{table_name}_org_bank_as_of",
        table_name,
        ["organization_id", "bank_id", "as_of_date"],
    )
    op.create_index(
        f"ix_{table_name}_org_batch",
        table_name,
        ["organization_id", "ingestion_batch_id"],
    )


def _current_generation_unique(table_name: str, columns: list[str]) -> None:
    op.create_index(
        f"uq_{table_name}_current",
        table_name,
        columns,
        unique=True,
        postgresql_where=sa.text("superseded_by IS NULL"),
    )


def upgrade() -> None:
    op.create_table(
        "mapping_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name="ck_mapping_configs_status",
        ),
        sa.CheckConstraint(
            f"source_system IN ({SOURCE_SYSTEMS})",
            name="ck_mapping_configs_source_system",
        ),
        sa.CheckConstraint("version >= 1", name="ck_mapping_configs_version_positive"),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_mapping_configs_id_org"),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "source_system",
            "version",
            name="uq_mapping_configs_scope_version",
        ),
    )
    op.create_index(
        "uq_mapping_configs_single_active",
        "mapping_configs",
        ["organization_id", "bank_id", "source_system"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "ingestion_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("adapter_version", sa.String(length=40), nullable=False),
        sa.Column("extraction_mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("stored_object_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("mapping_config_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("records_extracted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_translated", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_accepted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_warning", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_error", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_blocked", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("validation_report", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('created', 'extracting', 'translating', 'validating', "
            "'accepted', 'accepted_with_warnings', 'rejected', 'failed')",
            name="ck_ingestion_batches_status",
        ),
        sa.CheckConstraint(
            f"source_system IN ({SOURCE_SYSTEMS})",
            name="ck_ingestion_batches_source_system",
        ),
        sa.CheckConstraint(
            "extraction_mode IN ('full', 'incremental')",
            name="ck_ingestion_batches_extraction_mode",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.ForeignKeyConstraint(["stored_object_id"], ["stored_objects.id"]),
        sa.ForeignKeyConstraint(["mapping_config_id"], ["mapping_configs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_ingestion_batches_id_org"),
    )
    op.create_index(
        "ix_ingestion_batches_org_bank_as_of",
        "ingestion_batches",
        ["organization_id", "bank_id", "as_of_date"],
    )
    op.create_index(
        "ix_ingestion_batches_org_content_hash",
        "ingestion_batches",
        ["organization_id", "content_hash"],
    )

    op.create_table(
        "lineage_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation_type", sa.String(length=24), nullable=False),
        sa.Column("operation_ref", sa.String(length=255), nullable=False),
        sa.Column("input_lineage_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation_type IN ('ADAPTER_EXTRACT', 'ADAPTER_TRANSLATE', 'VALIDATION', "
            "'ENRICHMENT', 'ML_ENRICHMENT', 'HUMAN_OVERRIDE', 'MANUAL_ENTRY', 'SUPERSESSION')",
            name="ck_lineage_records_operation_type",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id", "organization_id"],
            ["ingestion_batches.id", "ingestion_batches.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_lineage_records_id_org"),
    )
    op.create_index(
        "ix_lineage_records_org_batch",
        "lineage_records",
        ["organization_id", "ingestion_batch_id"],
    )

    op.create_table(
        "translation_failures",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("source_locator", sa.String(length=255), nullable=False),
        sa.Column("raw_record", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id", "organization_id"],
            ["ingestion_batches.id", "ingestion_batches.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_translation_failures_org_batch",
        "translation_failures",
        ["organization_id", "ingestion_batch_id"],
    )

    op.create_table(
        "canonical_gl_accounts",
        *_canonical_metadata_columns(),
        sa.Column("account_code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("account_class", sa.String(length=16), nullable=False),
        sa.Column("parent_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("balance", sa.Numeric(28, 6), nullable=True),
        sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.CheckConstraint(
            "account_class IN ('ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE', 'OFF_BALANCE')",
            name="ck_canonical_gl_accounts_account_class",
        ),
        sa.ForeignKeyConstraint(
            ["parent_account_id", "organization_id"],
            ["canonical_gl_accounts.id", "canonical_gl_accounts.organization_id"],
        ),
        *_canonical_shared_constraints("canonical_gl_accounts"),
    )
    _canonical_shared_indexes("canonical_gl_accounts")
    _current_generation_unique(
        "canonical_gl_accounts",
        ["organization_id", "bank_id", "account_code", "as_of_date"],
    )

    op.create_table(
        "canonical_counterparties",
        *_canonical_metadata_columns(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("counterparty_type", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("rating", sa.String(length=16), nullable=True),
        sa.Column("rating_source", sa.String(length=40), nullable=True),
        sa.Column("group_reference", sa.String(length=255), nullable=True),
        sa.Column(
            "external_identifiers", sa.JSON(), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.CheckConstraint(
            "counterparty_type IN ('RETAIL_INDIVIDUAL', 'SME', 'CORPORATE', 'BANK_OECD', "
            "'BANK_NON_OECD', 'CENTRAL_BANK', 'SOVEREIGN', 'GOVERNMENT_ENTITY', "
            "'MULTILATERAL_DEV_BANK', 'NBFI', 'OTHER')",
            name="ck_canonical_counterparties_counterparty_type",
        ),
        *_canonical_shared_constraints("canonical_counterparties"),
    )
    _canonical_shared_indexes("canonical_counterparties")
    _current_generation_unique(
        "canonical_counterparties",
        ["organization_id", "bank_id", "source_system", "source_reference", "as_of_date"],
    )

    op.create_table(
        "canonical_products",
        *_canonical_metadata_columns(),
        sa.Column("product_code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("regulatory_category", sa.String(length=80), nullable=True),
        sa.Column("risk_weight_code", sa.String(length=16), nullable=True),
        sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        *_canonical_shared_constraints("canonical_products"),
    )
    _canonical_shared_indexes("canonical_products")
    _current_generation_unique(
        "canonical_products",
        ["organization_id", "bank_id", "product_code", "as_of_date"],
    )

    op.create_table(
        "canonical_positions",
        *_canonical_metadata_columns(),
        sa.Column("position_type", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("origination_date", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "position_type IN ('LOAN', 'DEPOSIT', 'SECURITY_HOLDING', 'DERIVATIVE', 'CASH', "
            "'INTERBANK_PLACEMENT', 'INTERBANK_BORROWING', 'LC_GUARANTEE', "
            "'COMMITMENT_UNDRAWN', 'OTHER_ASSET', 'OTHER_LIABILITY')",
            name="ck_canonical_positions_position_type",
        ),
        *_canonical_shared_constraints("canonical_positions"),
    )
    _canonical_shared_indexes("canonical_positions")
    _current_generation_unique(
        "canonical_positions",
        ["organization_id", "bank_id", "source_system", "source_reference"],
    )

    op.create_table(
        "canonical_position_snapshots",
        *_canonical_metadata_columns(),
        sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("counterparty_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gl_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("balance", sa.Numeric(28, 6), nullable=False),
        sa.Column("notional", sa.Numeric(28, 6), nullable=True),
        sa.Column("interest_rate", sa.Numeric(18, 10), nullable=True),
        sa.Column("rate_type", sa.String(length=16), nullable=True),
        sa.Column("rate_index", sa.String(length=40), nullable=True),
        sa.Column("rate_spread", sa.Numeric(18, 10), nullable=True),
        sa.Column("contractual_maturity", sa.Date(), nullable=True),
        sa.Column("next_repricing_date", sa.Date(), nullable=True),
        sa.Column("ifrs9_stage", sa.Integer(), nullable=True),
        sa.Column("behavioral_maturity_months", sa.Integer(), nullable=True),
        sa.Column(
            "enrichment_provenance", sa.JSON(), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.CheckConstraint(
            "rate_type IN ('FIXED', 'FLOATING') OR rate_type IS NULL",
            name="ck_canonical_position_snapshots_rate_type",
        ),
        sa.CheckConstraint(
            "ifrs9_stage IN (1, 2, 3) OR ifrs9_stage IS NULL",
            name="ck_canonical_position_snapshots_ifrs9_stage",
        ),
        sa.ForeignKeyConstraint(
            ["position_id", "organization_id"],
            ["canonical_positions.id", "canonical_positions.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_id", "organization_id"],
            ["canonical_counterparties.id", "canonical_counterparties.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["canonical_products.id", "canonical_products.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["gl_account_id", "organization_id"],
            ["canonical_gl_accounts.id", "canonical_gl_accounts.organization_id"],
        ),
        *_canonical_shared_constraints("canonical_position_snapshots"),
    )
    _canonical_shared_indexes("canonical_position_snapshots")
    _current_generation_unique(
        "canonical_position_snapshots",
        ["organization_id", "position_id", "as_of_date"],
    )
    op.create_index(
        "ix_canonical_position_snapshots_org_position",
        "canonical_position_snapshots",
        ["organization_id", "position_id"],
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation
            ON {table}
            USING (organization_id = {TENANT_ID_EXPR})
            WITH CHECK (organization_id = {TENANT_ID_EXPR})
            """
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")

    op.drop_table("canonical_position_snapshots")
    op.drop_table("canonical_positions")
    op.drop_table("canonical_products")
    op.drop_table("canonical_counterparties")
    op.drop_table("canonical_gl_accounts")
    op.drop_table("translation_failures")
    op.drop_table("lineage_records")
    op.drop_table("ingestion_batches")
    op.drop_table("mapping_configs")
