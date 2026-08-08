"""LRMD ¶60–63 internal liquidity-value (haircut) schedule

Revision ID: 202608070033
Revises: 202608070032

``param_liquidity_haircut`` is the institution's internal liquidity-value
schedule: estimated haircuts per asset class, effective-dated with the same
approval evidence the other parameter tables carry (¶62(b) requires Senior
Management to re-assess the values at least annually). LMTD Table 9's
Estimated Haircut and Monetized Value columns resolve from here.

Same post-epoch text RLS policy as 202608070032; no seed rows — a class with
no active row reports a zero haircut with the gap noted on the template.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608070033"
down_revision = "202608070032"
branch_labels = None
depends_on = None

_TABLE = "param_liquidity_haircut"
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=8), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("approved_by", sa.String(length=120), nullable=False),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_class", sa.String(length=80), nullable=False),
        sa.Column("haircut_pct", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "asset_class",
            "effective_from",
            name="uq_param_liquidity_haircut_scope",
        ),
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
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_table(_TABLE)
