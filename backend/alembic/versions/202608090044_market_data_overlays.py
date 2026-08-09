"""per-bank market data overlays

Revision ID: 202608090044
Revises: 202608090043

The tenant-side half of the two-layer market data architecture
(AequorOS_Market_Data_and_Curve_Platform.md §2, §9): one RLS-FORCED table
of effective-dated, component-tagged spread adjustments a bank layers on
the AequorOS golden copy. Composition happens at read time; golden data is
never written. Rows are append-only versioned (``superseded_by``); ending
an overlay sets ``effective_to``.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608090044"
down_revision = "202608090043"
branch_labels = None
depends_on = None

# Post-platform-ID-epoch policy form: text comparison, never a ::uuid cast.
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"
_TABLE = "market_data_overlays"

_BASE_REF_KINDS = "('curve', 'fx', 'index')"
_ADJUSTMENT_TYPES = "('additive_bps', 'fixed', 'multiplicative')"
_COMPONENT_TAGS = (
    "('liquidity_premium', 'term_liquidity_premium', 'funding_spread', 'credit_spread', 'other')"
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("base_ref_kind", sa.String(length=10), nullable=False),
        sa.Column("base_curve_name", sa.String(length=80), nullable=True),
        sa.Column("tenor_months", sa.Integer(), nullable=True),
        sa.Column("adjustment_type", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("component_tag", sa.String(length=30), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"base_ref_kind IN {_BASE_REF_KINDS}",
            name="ck_market_data_overlays_base_ref_kind",
        ),
        sa.CheckConstraint(
            f"adjustment_type IN {_ADJUSTMENT_TYPES}",
            name="ck_market_data_overlays_adjustment_type",
        ),
        sa.CheckConstraint(
            f"component_tag IN {_COMPONENT_TAGS}",
            name="ck_market_data_overlays_component_tag",
        ),
        sa.CheckConstraint(
            "(base_ref_kind = 'curve') = (base_curve_name IS NOT NULL)",
            name="ck_market_data_overlays_curve_name",
        ),
        sa.CheckConstraint(
            "tenor_months IS NULL OR tenor_months > 0",
            name="ck_market_data_overlays_tenor_months",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_market_data_overlays_effective_window",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_market_data_overlays_id_org"),
    )
    op.create_index(
        "ix_market_data_overlays_scope",
        _TABLE,
        ["organization_id", "bank_id", "base_ref_kind", "base_curve_name"],
    )

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
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.drop_index("ix_market_data_overlays_scope", table_name=_TABLE)
    op.drop_table(_TABLE)
