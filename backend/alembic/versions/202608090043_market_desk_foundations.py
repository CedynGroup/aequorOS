"""market desk foundations

Revision ID: 202608090043
Revises: 202608090042
Create Date: 2026-08-09 21:00:00.000000

Market research desk structural spine (AequorOS_Market_Data_and_Curve_
Platform.md §2, §4, §5, §9, §10):

1. Widens every ``source_system`` CHECK with the desk-as-vendor system
   (``AEQUOR_DESK``) — the twelve tables 202607170006 left carrying the
   constraint (its seven widened tables plus the five canonical market-data
   entity tables it created).
2. Widens the ``market_data_connections`` vendor CHECK with ``aequor_desk``
   (valid provenance vocabulary; the bank-facing API deliberately does not
   offer it — desk data is pushed centrally at publication).
3. Widens the ``canonical_yield_curves`` curve-type CHECK with the
   desk-constructed families: ``zero`` / ``forward`` / ``discount``
   (AEQ.GHS.SOV.ZERO / AEQ.GHS.SOV.FWD / AEQ.GHS.OIS, spec §8).
4. Creates the five GLOBAL ``desk_*`` tables (silver captures, cleaned
   observations, the methodology register, bitemporal determinations,
   publication fan-out records). Deliberately NOT tenant-scoped and NOT
   RLS-forced — the ``jurisdictions`` registry precedent: desk state is
   AequorOS-owned, upstream of every tenant, and never read by tenant
   sessions.

``downgrade`` drops the desk tables and removes AEQUOR_DESK-sourced rows and
desk-typed curves in FK dependency order before restoring the narrower
constraints (the 202607170006 pattern).
"""

import sqlalchemy as sa

from alembic import op

revision = "202608090043"
down_revision = "202608090042"
branch_labels = None
depends_on = None

PREVIOUS_SYSTEMS = (
    "'EXCEL_CSV', 'T24', 'FINACLE', 'FLEXCUBE', 'DB_DIRECT', 'SFTP_DROP', 'API_GENERIC', "
    "'API_PUSH', 'BLOOMBERG', 'REFINITIV', 'MANUAL_UPLOAD', 'MANUAL'"
)
WIDENED_SYSTEMS = (
    "'EXCEL_CSV', 'T24', 'FINACLE', 'FLEXCUBE', 'DB_DIRECT', 'SFTP_DROP', 'API_GENERIC', "
    "'API_PUSH', 'BLOOMBERG', 'REFINITIV', 'MANUAL_UPLOAD', 'MANUAL', 'AEQUOR_DESK'"
)
NEW_SYSTEM = "'AEQUOR_DESK'"

# Every table carrying ck_<table>_source_system after 202607170006.
CHECK_WIDENED_TABLES = (
    "mapping_configs",
    "ingestion_batches",
    "canonical_gl_accounts",
    "canonical_counterparties",
    "canonical_products",
    "canonical_positions",
    "canonical_position_snapshots",
    "canonical_yield_curves",
    "canonical_yield_curve_points",
    "canonical_fx_rates",
    "canonical_market_indices",
    "canonical_counterparty_ratings",
)

PREVIOUS_VENDORS = "'bloomberg', 'refinitiv', 'manual_upload'"
WIDENED_VENDORS = "'bloomberg', 'refinitiv', 'manual_upload', 'aequor_desk'"

PREVIOUS_CURVE_TYPES = "'sovereign', 'interbank', 'swap', 'credit_spread'"
WIDENED_CURVE_TYPES = (
    "'sovereign', 'interbank', 'swap', 'credit_spread', 'zero', 'forward', 'discount'"
)
NEW_CURVE_TYPES = "'zero', 'forward', 'discount'"

DESK_TABLES = (
    "desk_publications",
    "desk_determinations",
    "desk_observations",
    "desk_source_captures",
    "desk_methodologies",
)


def _swap_check(table: str, constraint: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_constraint(constraint, type_="check")
        batch_op.create_check_constraint(constraint, expression)


def _swap_source_system_checks(systems: str) -> None:
    for table in CHECK_WIDENED_TABLES:
        _swap_check(table, f"ck_{table}_source_system", f"source_system IN ({systems})")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    _swap_source_system_checks(WIDENED_SYSTEMS)
    _swap_check(
        "market_data_connections",
        "ck_market_data_connections_vendor",
        f"vendor IN ({WIDENED_VENDORS})",
    )
    _swap_check(
        "canonical_yield_curves",
        "ck_canonical_yield_curves_curve_type",
        f"curve_type IN ({WIDENED_CURVE_TYPES})",
    )

    op.create_table(
        "desk_source_captures",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("source_key", sa.String(length=60), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("storage_path", sa.String(length=500), nullable=True),
        sa.Column("parser_version", sa.String(length=40), nullable=False),
        sa.Column(
            "status", sa.String(length=12), server_default=sa.text("'captured'"), nullable=False
        ),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=320), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('captured', 'parsed', 'failed')",
            name="ck_desk_source_captures_status",
        ),
    )
    op.create_index(
        "ix_desk_source_captures_source_key_as_of",
        "desk_source_captures",
        ["source_key", "as_of_date"],
    )

    op.create_table(
        "desk_observations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("capture_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("series_code", sa.String(length=80), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(28, 10), nullable=False),
        sa.Column("unit", sa.String(length=12), nullable=False),
        sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("quality_flags", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("entered_by", sa.String(length=320), nullable=True),
        sa.Column("superseded_by", sa.Uuid(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "unit IN ('pct', 'rate', 'ghs', 'index')",
            name="ck_desk_observations_unit",
        ),
        sa.ForeignKeyConstraint(["capture_id"], ["desk_source_captures.id"]),
    )
    op.create_index(
        "ix_desk_observations_series_as_of",
        "desk_observations",
        ["series_code", "as_of_date"],
    )
    # Current-generation uniqueness (the canonical-store idiom): exactly one
    # non-superseded observation per (series, as-of).
    op.create_index(
        "uq_desk_observations_current",
        "desk_observations",
        ["series_code", "as_of_date"],
        unique=True,
        postgresql_where=sa.text("superseded_by IS NULL"),
        sqlite_where=sa.text("superseded_by IS NULL"),
    )

    op.create_table(
        "desk_methodologies",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("methodology_code", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=12), server_default=sa.text("'draft'"), nullable=False
        ),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("change_rationale", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.String(length=320), nullable=False),
        sa.Column("approved_by", sa.String(length=320), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'retired')",
            name="ck_desk_methodologies_status",
        ),
        sa.UniqueConstraint(
            "methodology_code", "version", name="uq_desk_methodologies_code_version"
        ),
    )

    op.create_table(
        "desk_determinations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("cob_date", sa.Date(), nullable=False),
        sa.Column("methodology_code", sa.String(length=40), nullable=False),
        sa.Column("methodology_version", sa.Integer(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("derived_values", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("qa_results", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'draft'"), nullable=False
        ),
        sa.Column("prepared_by", sa.String(length=320), nullable=False),
        sa.Column("reviewed_by", sa.String(length=320), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'rejected', 'published')",
            name="ck_desk_determinations_status",
        ),
        sa.ForeignKeyConstraint(["supersedes_id"], ["desk_determinations.id"]),
    )
    op.create_index(
        "ix_desk_determinations_cob_date", "desk_determinations", ["cob_date"]
    )

    op.create_table(
        "desk_publications",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("determination_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("published_by", sa.String(length=320), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("results", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'failed')",
            name="ck_desk_publications_status",
        ),
        sa.ForeignKeyConstraint(["determination_id"], ["desk_determinations.id"]),
    )
    op.create_index(
        "ix_desk_publications_determination_id", "desk_publications", ["determination_id"]
    )


def downgrade() -> None:
    for table in DESK_TABLES:
        op.drop_table(table)

    # Remove desk-sourced canonical market data rows in FK dependency order
    # (202607170006 pattern). Desk pulls write only market-data entities, and
    # desk curve names are the AEQ.* codes, so no cross-source supersession
    # pointers into these rows can exist — but null them defensively anyway.
    market_tables = (
        "canonical_yield_curve_points",
        "canonical_yield_curves",
        "canonical_fx_rates",
        "canonical_market_indices",
        "canonical_counterparty_ratings",
    )
    for table in market_tables:
        op.execute(
            f"UPDATE {table} SET superseded_by = NULL WHERE superseded_by IN "
            f"(SELECT id FROM {table} WHERE source_system = {NEW_SYSTEM})"
        )
    op.execute(
        f"DELETE FROM canonical_yield_curve_points WHERE source_system = {NEW_SYSTEM}"
    )
    # Desk-typed curves from ANY source must go before the CHECK narrows.
    op.execute(
        "DELETE FROM canonical_yield_curve_points WHERE yield_curve_id IN "
        f"(SELECT id FROM canonical_yield_curves WHERE curve_type IN ({NEW_CURVE_TYPES}))"
    )
    op.execute(
        "UPDATE canonical_yield_curves SET superseded_by = NULL WHERE superseded_by IN "
        f"(SELECT id FROM canonical_yield_curves WHERE curve_type IN ({NEW_CURVE_TYPES}))"
    )
    op.execute(f"DELETE FROM canonical_yield_curves WHERE source_system = {NEW_SYSTEM}")
    op.execute(
        f"DELETE FROM canonical_yield_curves WHERE curve_type IN ({NEW_CURVE_TYPES})"
    )
    op.execute(f"DELETE FROM canonical_fx_rates WHERE source_system = {NEW_SYSTEM}")
    op.execute(f"DELETE FROM canonical_market_indices WHERE source_system = {NEW_SYSTEM}")
    op.execute(
        f"DELETE FROM canonical_counterparty_ratings WHERE source_system = {NEW_SYSTEM}"
    )
    op.execute(
        "DELETE FROM translation_failures WHERE ingestion_batch_id IN "
        f"(SELECT id FROM ingestion_batches WHERE source_system = {NEW_SYSTEM})"
    )
    op.execute(
        "DELETE FROM lineage_records WHERE ingestion_batch_id IN "
        f"(SELECT id FROM ingestion_batches WHERE source_system = {NEW_SYSTEM})"
    )
    op.execute(f"DELETE FROM ingestion_batches WHERE source_system = {NEW_SYSTEM}")
    op.execute(f"DELETE FROM mapping_configs WHERE source_system = {NEW_SYSTEM}")
    op.execute("DELETE FROM market_data_quota_usage WHERE vendor = 'aequor_desk'")
    op.execute("DELETE FROM market_data_connections WHERE vendor = 'aequor_desk'")

    _swap_check(
        "canonical_yield_curves",
        "ck_canonical_yield_curves_curve_type",
        f"curve_type IN ({PREVIOUS_CURVE_TYPES})",
    )
    _swap_check(
        "market_data_connections",
        "ck_market_data_connections_vendor",
        f"vendor IN ({PREVIOUS_VENDORS})",
    )
    _swap_source_system_checks(PREVIOUS_SYSTEMS)
