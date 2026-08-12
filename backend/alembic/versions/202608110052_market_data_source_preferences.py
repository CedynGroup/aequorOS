"""Per-bank market-data source preference (market_data_sources.md §2)

Revision ID: 202608110052
Revises: 202608110051

One RLS-forced tenant table, ``market_data_source_preferences``: a single
row per bank recording, per category (curves/fx/rates), the selected base
plane (aequor/bank/vendor) and whether the private overlay layer composes.
The arbitration getters honour the selection so it flows live into IRRBB/FTP
(spec §3). No seed rows — the absent-row default (aequor + overlay on) is
synthesised in the service (spec §2 Defaults).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608110052"
down_revision = "202608110051"
branch_labels = None
depends_on = None

# Post-platform-ID-epoch policy form (202607250027 precedent): organization_id
# is OR- text, so the policy compares text — never a ::uuid cast.
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

_TABLE = "market_data_source_preferences"


def _force_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("curves_source", sa.String(length=8), nullable=False),
        sa.Column("fx_source", sa.String(length=8), nullable=False),
        sa.Column("rates_source", sa.String(length=8), nullable=False),
        sa.Column("curves_overlay", sa.Boolean(), nullable=False),
        sa.Column("fx_overlay", sa.Boolean(), nullable=False),
        sa.Column("rates_overlay", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "curves_source IN ('aequor', 'bank', 'vendor')",
            name="ck_market_data_source_preferences_curves_source",
        ),
        sa.CheckConstraint(
            "fx_source IN ('aequor', 'bank', 'vendor')",
            name="ck_market_data_source_preferences_fx_source",
        ),
        sa.CheckConstraint(
            "rates_source IN ('aequor', 'bank', 'vendor')",
            name="ck_market_data_source_preferences_rates_source",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", name="uq_market_data_source_preferences_bank"
        ),
    )
    op.create_index(
        "ix_market_data_source_preferences_bank",
        _TABLE,
        ["organization_id", "bank_id"],
    )
    _force_rls(_TABLE)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.drop_table(_TABLE)
