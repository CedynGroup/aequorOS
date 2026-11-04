"""market data entities

Revision ID: 202607170006
Revises: 202607170005
Create Date: 2026-07-17 22:00:00.000000

Market Data Adapter schema slice (market_data_adapter.md §13, data_engine.md
§4.2 "Market Data"): five canonical entities (yield curves + points, FX rates,
market indices, counterparty ratings) carrying the full mandatory-metadata
contract, plus the two operational tables the adapter framework needs
(vendor connections with credential lifecycle, per-month quota ledger).

Also widens every existing ``source_system`` CHECK with the market-data
vendors (``BLOOMBERG``, ``REFINITIV``, ``MANUAL_UPLOAD``) — same seven tables
202607170004 widened for API_PUSH. Additive-only on upgrade; ``downgrade``
drops the new tables and removes vendor-sourced rows in FK dependency order
before restoring the narrower constraints.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607170006"
down_revision = "202607170005"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"

PREVIOUS_SYSTEMS = (
    "'EXCEL_CSV', 'T24', 'FINACLE', 'FLEXCUBE', 'DB_DIRECT', 'SFTP_DROP', 'API_GENERIC', "
    "'API_PUSH', 'MANUAL'"
)
WIDENED_SYSTEMS = (
    "'EXCEL_CSV', 'T24', 'FINACLE', 'FLEXCUBE', 'DB_DIRECT', 'SFTP_DROP', 'API_GENERIC', "
    "'API_PUSH', 'BLOOMBERG', 'REFINITIV', 'MANUAL_UPLOAD', 'MANUAL'"
)
NEW_SYSTEMS = "'BLOOMBERG', 'REFINITIV', 'MANUAL_UPLOAD'"
VALIDATION_STATUSES = "'pending', 'accepted', 'warning', 'error', 'blocked'"

CHECK_WIDENED_TABLES = (
    "mapping_configs",
    "ingestion_batches",
    "canonical_gl_accounts",
    "canonical_counterparties",
    "canonical_products",
    "canonical_positions",
    "canonical_position_snapshots",
)

NEW_TABLES = (
    "canonical_yield_curves",
    "canonical_yield_curve_points",
    "canonical_fx_rates",
    "canonical_market_indices",
    "canonical_counterparty_ratings",
    "market_data_connections",
    "market_data_quota_usage",
)

CONNECTION_STATUSES = (
    "'TESTING', 'ACTIVE', 'EXPIRING_SOON', 'EXPIRED', 'REVOKED', 'INVALID', "
    "'REPLACED_PENDING_DELETION', 'DISABLED'"
)
VENDORS = "'bloomberg', 'refinitiv', 'manual_upload'"


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
            f"source_system IN ({WIDENED_SYSTEMS})",
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


def _current_generation_unique(table_name: str, columns: list) -> None:
    op.create_index(
        f"uq_{table_name}_current",
        table_name,
        columns,
        unique=True,
        postgresql_where=sa.text("superseded_by IS NULL"),
    )


def _swap_source_system_checks(systems: str) -> None:
    for table in CHECK_WIDENED_TABLES:
        constraint = f"ck_{table}_source_system"
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(constraint, type_="check")
            batch_op.create_check_constraint(constraint, f"source_system IN ({systems})")


def upgrade() -> None:
    _swap_source_system_checks(WIDENED_SYSTEMS)

    op.create_table(
        "canonical_yield_curves",
        *_canonical_metadata_columns(),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("curve_name", sa.String(length=80), nullable=False),
        sa.Column("curve_type", sa.String(length=24), nullable=False),
        sa.CheckConstraint(
            "curve_type IN ('sovereign', 'interbank', 'swap', 'credit_spread')",
            name="ck_canonical_yield_curves_curve_type",
        ),
        *_canonical_shared_constraints("canonical_yield_curves"),
    )
    _canonical_shared_indexes("canonical_yield_curves")
    _current_generation_unique(
        "canonical_yield_curves",
        ["organization_id", "bank_id", "as_of_date", "currency", "curve_name"],
    )

    op.create_table(
        "canonical_yield_curve_points",
        *_canonical_metadata_columns(),
        sa.Column("yield_curve_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenor_months", sa.Integer(), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.CheckConstraint(
            "tenor_months > 0",
            name="ck_canonical_yield_curve_points_tenor_months",
        ),
        sa.CheckConstraint(
            "rate >= -1 AND rate <= 1",
            name="ck_canonical_yield_curve_points_rate",
        ),
        sa.ForeignKeyConstraint(
            ["yield_curve_id", "organization_id"],
            ["canonical_yield_curves.id", "canonical_yield_curves.organization_id"],
        ),
        *_canonical_shared_constraints("canonical_yield_curve_points"),
    )
    _canonical_shared_indexes("canonical_yield_curve_points")
    _current_generation_unique(
        "canonical_yield_curve_points",
        ["organization_id", "yield_curve_id", "tenor_months"],
    )
    op.create_index(
        "ix_canonical_yield_curve_points_org_curve",
        "canonical_yield_curve_points",
        ["organization_id", "yield_curve_id"],
    )

    op.create_table(
        "canonical_fx_rates",
        *_canonical_metadata_columns(),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate_type", sa.String(length=12), nullable=False),
        sa.Column("tenor_months", sa.Integer(), nullable=True),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.CheckConstraint(
            "rate_type IN ('spot', 'forward')",
            name="ck_canonical_fx_rates_rate_type",
        ),
        sa.CheckConstraint(
            "(rate_type = 'spot') = (tenor_months IS NULL)",
            name="ck_canonical_fx_rates_spot_tenor",
        ),
        sa.CheckConstraint("rate > 0", name="ck_canonical_fx_rates_rate_positive"),
        *_canonical_shared_constraints("canonical_fx_rates"),
    )
    _canonical_shared_indexes("canonical_fx_rates")
    _current_generation_unique(
        "canonical_fx_rates",
        [
            "organization_id",
            "bank_id",
            "as_of_date",
            "base_currency",
            "quote_currency",
            "rate_type",
            sa.text("coalesce(tenor_months, 0)"),
        ],
    )

    op.create_table(
        "canonical_market_indices",
        *_canonical_metadata_columns(),
        sa.Column("index_code", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Numeric(28, 6), nullable=False),
        sa.Column("scenario", sa.String(length=24), nullable=False),
        sa.Column("horizon_months", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "scenario IN ('base', 'adverse', 'severely_adverse')",
            name="ck_canonical_market_indices_scenario",
        ),
        *_canonical_shared_constraints("canonical_market_indices"),
    )
    _canonical_shared_indexes("canonical_market_indices")
    _current_generation_unique(
        "canonical_market_indices",
        [
            "organization_id",
            "bank_id",
            "as_of_date",
            "index_code",
            "scenario",
            sa.text("coalesce(horizon_months, 0)"),
        ],
    )

    op.create_table(
        "canonical_counterparty_ratings",
        *_canonical_metadata_columns(),
        sa.Column("issuer", sa.String(length=120), nullable=False),
        sa.Column("agency", sa.String(length=24), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("watch_status", sa.String(length=16), nullable=True),
        sa.Column("rating_date", sa.Date(), nullable=False),
        sa.CheckConstraint(
            "agency IN ('moodys', 'sp', 'fitch', 'internal')",
            name="ck_canonical_counterparty_ratings_agency",
        ),
        sa.CheckConstraint(
            "watch_status IN ('positive', 'negative', 'stable', 'developing') "
            "OR watch_status IS NULL",
            name="ck_canonical_counterparty_ratings_watch_status",
        ),
        *_canonical_shared_constraints("canonical_counterparty_ratings"),
    )
    _canonical_shared_indexes("canonical_counterparty_ratings")
    _current_generation_unique(
        "canonical_counterparty_ratings",
        ["organization_id", "bank_id", "as_of_date", "issuer", "agency"],
    )

    op.create_table(
        "market_data_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor", sa.String(length=20), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'TESTING'"),
            nullable=False,
        ),
        sa.Column("credential_ciphertext", sa.Text(), nullable=True),
        sa.Column("credential_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("vault_path", sa.String(length=255), nullable=False),
        sa.Column("scopes", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("schedule", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pull_status", sa.String(length=20), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"vendor IN ({VENDORS})",
            name="ck_market_data_connections_vendor",
        ),
        sa.CheckConstraint(
            f"status IN ({CONNECTION_STATUSES})",
            name="ck_market_data_connections_status",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_market_data_connections_id_org"),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "vendor",
            "display_name",
            name="uq_market_data_connections_scope_name",
        ),
    )
    op.create_index(
        "ix_market_data_connections_org_bank",
        "market_data_connections",
        ["organization_id", "bank_id"],
    )

    op.create_table(
        "market_data_quota_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor", sa.String(length=20), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("units_consumed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("monthly_cap", sa.Integer(), nullable=True),
        sa.Column("pull_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_market_data_quota_usage_id_org"),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "vendor",
            "month",
            name="uq_market_data_quota_usage_scope_month",
        ),
    )
    op.create_index(
        "ix_market_data_quota_usage_org_bank",
        "market_data_quota_usage",
        ["organization_id", "bank_id"],
    )

    for table in NEW_TABLES:
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
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("market_data_quota_usage")
    op.drop_table("market_data_connections")
    op.drop_table("canonical_counterparty_ratings")
    op.drop_table("canonical_market_indices")
    op.drop_table("canonical_fx_rates")
    op.drop_table("canonical_yield_curve_points")
    op.drop_table("canonical_yield_curves")

    # Remove vendor-sourced rows from the surviving tables in FK dependency
    # order (same approach as 202607170004): null cross-source references
    # into vendor rows, delete the vendor rows, then the batch machinery.
    for column, dimension in (
        ("counterparty_id", "canonical_counterparties"),
        ("product_id", "canonical_products"),
        ("gl_account_id", "canonical_gl_accounts"),
    ):
        op.execute(
            f"UPDATE canonical_position_snapshots SET {column} = NULL WHERE {column} IN "
            f"(SELECT id FROM {dimension} WHERE source_system IN ({NEW_SYSTEMS}))"
        )
    op.execute(
        "DELETE FROM canonical_position_snapshots "
        f"WHERE source_system IN ({NEW_SYSTEMS})"
    )
    op.execute(f"DELETE FROM canonical_positions WHERE source_system IN ({NEW_SYSTEMS})")
    op.execute(
        "UPDATE canonical_gl_accounts SET parent_account_id = NULL WHERE parent_account_id IN "
        f"(SELECT id FROM canonical_gl_accounts WHERE source_system IN ({NEW_SYSTEMS}))"
    )
    for table in (
        "canonical_gl_accounts",
        "canonical_counterparties",
        "canonical_products",
    ):
        op.execute(
            f"UPDATE {table} SET superseded_by = NULL WHERE superseded_by IN "
            f"(SELECT id FROM {table} WHERE source_system IN ({NEW_SYSTEMS}))"
        )
        op.execute(f"DELETE FROM {table} WHERE source_system IN ({NEW_SYSTEMS})")
    op.execute(
        "DELETE FROM canonical_reference_rows WHERE ingestion_batch_id IN "
        f"(SELECT id FROM ingestion_batches WHERE source_system IN ({NEW_SYSTEMS}))"
    )
    op.execute(
        "DELETE FROM translation_failures WHERE ingestion_batch_id IN "
        f"(SELECT id FROM ingestion_batches WHERE source_system IN ({NEW_SYSTEMS}))"
    )
    op.execute(
        "DELETE FROM lineage_records WHERE ingestion_batch_id IN "
        f"(SELECT id FROM ingestion_batches WHERE source_system IN ({NEW_SYSTEMS}))"
    )
    op.execute(f"DELETE FROM ingestion_batches WHERE source_system IN ({NEW_SYSTEMS})")
    op.execute(f"DELETE FROM mapping_configs WHERE source_system IN ({NEW_SYSTEMS})")

    _swap_source_system_checks(PREVIOUS_SYSTEMS)
