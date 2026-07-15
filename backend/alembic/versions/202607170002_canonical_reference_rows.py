"""canonical reference rows

Revision ID: 202607170002
Revises: 202607170001
Create Date: 2026-07-17 12:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607170002"
down_revision = "202607170001"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"

TABLE = "canonical_reference_rows"

DATASET_KINDS = (
    "'capital_structure', 'behavioral_assumptions', 'yield_curve', 'fx_rates_current', "
    "'fx_rates_historical', 'historical_cashflows', 'historical_financials', "
    "'business_units', 'institution'"
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("dataset_kind", sa.String(length=40), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=False),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"dataset_kind IN ({DATASET_KINDS})",
            name=f"ck_{TABLE}_dataset_kind",
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
        sa.UniqueConstraint("id", "organization_id", name=f"uq_{TABLE}_id_org"),
        sa.UniqueConstraint(
            "ingestion_batch_id",
            "dataset_kind",
            "row_index",
            name=f"uq_{TABLE}_batch_kind_row",
        ),
    )
    op.create_index(
        f"ix_{TABLE}_org_bank_kind_as_of",
        TABLE,
        ["organization_id", "bank_id", "dataset_kind", "as_of_date"],
    )
    op.create_index(
        f"ix_{TABLE}_org_batch",
        TABLE,
        ["organization_id", "ingestion_batch_id"],
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
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index(f"ix_{TABLE}_org_batch", table_name=TABLE)
    op.drop_index(f"ix_{TABLE}_org_bank_kind_as_of", table_name=TABLE)
    op.drop_table(TABLE)
