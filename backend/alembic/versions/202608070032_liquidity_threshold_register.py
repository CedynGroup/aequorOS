"""LMTD ¶11(b)–(e) Board threshold register

Revision ID: 202608070032
Revises: 202608070031

``param_liquidity_threshold`` follows the RegulatoryParameterMixin shape the
other five parameter tables share: effective-dated generations scoped by
organization + jurisdiction, with ``approved_by``/``approval_timestamp`` as
the approval evidence. LMTD ¶11(b) makes the Board's internal thresholds for
the six monitoring tools an annual obligation from 2026-12-31; this table is
where an examiner's "show me your Board-approved thresholds" gets answered.

``institution_class`` distinguishes bank thresholds (monitoring tools) from
SDI thresholds (binding compliance ratios per ¶9) — same register, different
supervisory consequence. No seed rows here: defaults are the directive's
published minimums applied in code, and each institution's Board-adopted
values arrive through the audited register endpoint.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608070032"
down_revision = "202608070031"
branch_labels = None
depends_on = None

_TABLE = "param_liquidity_threshold"
# Post-platform-ID-epoch policy form (202607250027 precedent): organization_id
# is OR- text, so the policy compares text — never a ::uuid cast.
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
        sa.Column("institution_class", sa.String(length=8), nullable=False),
        sa.Column("threshold_code", sa.String(length=60), nullable=False),
        sa.Column("threshold_pct", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "institution_class IN ('bank', 'sdi')",
            name="ck_param_liquidity_threshold_institution_class",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "institution_class",
            "threshold_code",
            "effective_from",
            name="uq_param_liquidity_threshold_scope",
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
