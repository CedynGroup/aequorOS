"""Plane-2 EOD ladder: daily live-metric snapshots per (bank, day, module).

Upserted on every pipeline refresh — the day's last refresh is the close.
Desk prior-close deltas and daily sparklines read this series; the monthly
reporting spine and immutable regulatory runs are untouched.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608090041"
down_revision = "202608090040"
branch_labels = None
depends_on = None

# Post-platform-ID-epoch policy form: text comparison, never a ::uuid cast.
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"
_TABLE = "live_metric_snapshots"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("reporting_period_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("module", sa.String(length=16), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "module IN ('liquidity', 'capital', 'irr', 'fx', 'ftp', 'forecast')",
            name="ck_live_metric_snapshots_module",
        ),
        sa.CheckConstraint(
            "status IN ('green', 'amber', 'red', 'na')",
            name="ck_live_metric_snapshots_status",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            "snapshot_date",
            "module",
            name="uq_live_metric_snapshots_day",
        ),
    )
    op.create_index(
        "ix_live_metric_snapshots_series",
        _TABLE,
        ["organization_id", "bank_id", "module", "snapshot_date"],
    )
    if op.get_bind().dialect.name == "postgresql":
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
    op.drop_index("ix_live_metric_snapshots_series", table_name=_TABLE)
    op.drop_table(_TABLE)
