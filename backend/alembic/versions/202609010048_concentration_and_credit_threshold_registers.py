"""Board concentration-limit + credit-threshold registers (credit PR-3).

The BoG Guidelines on Management and Measurement of Credit Concentration Risk
(Sept 2025; banks, savings & loans, finance houses; framework due 31 Dec 2026)
require a Board limit structure per concentration dimension with breach
escalation, but prescribe NO numeric values — so both registers are created
EMPTY: every row is a Board decision carrying the mixin's approval evidence,
and an absent limit renders "Not set" on the monitor, never an invented
number. ``param_credit_threshold`` holds the credit early-warning trigger
levels on the same footing.

Both tables are tenant board registers, so they are RLS-FORCED like every
``param_*`` sibling (text GUC comparison, fail-closed on an unset GUC).

Revision ID: 202609010048
Revises: 202609010047
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609010048"
down_revision = "202609010047"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

_TABLES = ("param_concentration_limit", "param_credit_threshold")


def _mixin_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(16),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("jurisdiction_code", sa.String(8), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("approved_by", sa.String(120), nullable=False),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "param_concentration_limit",
        *_mixin_columns(),
        sa.Column("dimension", sa.String(24), nullable=False),
        sa.Column("limit_kind", sa.String(24), nullable=False),
        sa.Column("bucket_key", sa.String(120), nullable=True),
        sa.Column("value", sa.Numeric(12, 6), nullable=False),
        sa.CheckConstraint(
            "dimension IN ('single_name', 'sector', 'geography', 'product', "
            "'collateral', 'funding', 'employer')",
            name="ck_param_concentration_limit_dimension",
        ),
        sa.CheckConstraint(
            "limit_kind IN ('share_of_book_pct', 'share_of_capital_pct', 'hhi')",
            name="ck_param_concentration_limit_kind",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "dimension",
            "limit_kind",
            "bucket_key",
            "effective_from",
            name="uq_param_concentration_limit_scope",
        ),
    )
    op.create_table(
        "param_credit_threshold",
        *_mixin_columns(),
        sa.Column("threshold_code", sa.String(60), nullable=False),
        sa.Column("value_pct", sa.Numeric(12, 6), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "threshold_code",
            "effective_from",
            name="uq_param_credit_threshold_scope",
        ),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
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


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    for table in reversed(_TABLES):
        op.drop_table(table)
