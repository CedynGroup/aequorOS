"""ICAAP capital plans + quarterly ILAAP snapshots (product.md §Phase 2 item 10)

Revision ID: 202608070038
Revises: 202608070037

``capital_plans``: versioned plan documents (Pillar-2 add-on register,
management actions, trigger framework) with the annual Board-approval trail.
``ilaap_snapshots``: the append-only quarterly ILAAP component
(LRMD ¶12/¶24/¶26 — refreshable, not an annual monolith).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608070038"
down_revision = "202608070037"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"
_TABLES = ("capital_plans", "ilaap_snapshots")


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
        "capital_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("prepared_by", sa.Uuid(), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approval_reference", sa.String(length=200), nullable=True),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_expires_at", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'superseded')", name="ck_capital_plans_status"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "version", name="uq_capital_plans_version"
        ),
    )
    op.create_index("ix_capital_plans_bank", "capital_plans", ["organization_id", "bank_id"])

    op.create_table(
        "ilaap_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("reporting_period_id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("adequate", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ilaap_snapshots_bank", "ilaap_snapshots", ["organization_id", "bank_id"])

    for table in _TABLES:
        _force_rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
