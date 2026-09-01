"""The loan-events plane: ``canonical_loan_events`` (credit PR-4).

Positions and snapshots hold STOCKS; BoG Notice BG/GOV/SEC/2025/23's monthly
report and BSD8's movement schedule need FLOWS — write-offs (wilful /
non-wilful), recoveries (by collateral class), restructures (by measure),
disbursements and repayments. This table is a full canonical entity: ingested
through the ordinary batch machinery with lineage, per-reference supersession
and withdrawal, RLS-forced like every tenant-scoped canonical table. The
facility link stays in source-reference terms (no FK) because an event
routinely arrives in a later batch than its loan and must not dangle when the
loan's row is superseded by a re-push. NO SEEDING — events enter through the
Data Engine only.

Revision ID: 202609010049
Revises: 202609010048
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609010049"
down_revision = "202609010048"
branch_labels = None
depends_on = None

_TABLE = "canonical_loan_events"
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"
_CURRENT = "superseded_by IS NULL AND withdrawn_at IS NULL"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_system", sa.String(40), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("ingestion_batch_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("validation_status", sa.String(16), nullable=False),
        sa.Column("lineage_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("superseded_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by_batch_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("withdrawal_reason", sa.Text(), nullable=True),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("event_subtype", sa.String(40), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("position_source_reference", sa.String(255), nullable=False),
        sa.Column("amount", sa.Numeric(28, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_ghs", sa.Numeric(28, 6), nullable=True),
        sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('DISBURSEMENT', 'REPAYMENT', 'WRITE_OFF', 'RECOVERY', "
            "'RESTRUCTURE')",
            name="ck_canonical_loan_events_event_type",
        ),
        sa.CheckConstraint(
            "validation_status IN ('pending', 'accepted', 'warning', 'error', 'blocked')",
            name="ck_canonical_loan_events_validation_status",
        ),
        sa.CheckConstraint(
            "source_system IN ('EXCEL_CSV', 'T24', 'FINACLE', 'FLEXCUBE', "
            "'DB_DIRECT', 'SFTP_DROP', 'API_GENERIC', 'API_PUSH', 'BLOOMBERG', "
            "'REFINITIV', 'MANUAL_UPLOAD', 'MANUAL', 'AEQUOR_DESK')",
            name="ck_canonical_loan_events_source_system",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id", "organization_id"],
            ["ingestion_batches.id", "ingestion_batches.organization_id"],
        ),
    )
    op.create_index(
        "uq_canonical_loan_events_current",
        _TABLE,
        ["organization_id", "bank_id", "source_system", "source_reference"],
        unique=True,
        postgresql_where=sa.text(_CURRENT),
        sqlite_where=sa.text(_CURRENT),
    )
    op.create_index(
        "ix_canonical_loan_events_current_org_bank_date",
        _TABLE,
        ["organization_id", "bank_id", "event_date"],
        postgresql_where=sa.text(_CURRENT),
        sqlite_where=sa.text(_CURRENT),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.drop_index("ix_canonical_loan_events_current_org_bank_date", table_name=_TABLE)
    op.drop_index("uq_canonical_loan_events_current", table_name=_TABLE)
    op.drop_table(_TABLE)
