"""EWI framework + CFP engine (LRMD 2026 ¶28(e)–(f), ¶70–77)

Revision ID: 202608070036
Revises: 202608070035

Three RLS-forced tenant tables:

- ``liquidity_ewi_indicators``: the bank's early-warning indicator register.
  The eight BoG-named starters (¶28(f)) are code-defined defaults; rows here
  override a starter's Board-set trigger levels or add custom indicators.
- ``contingency_funding_plans``: versioned CFP documents carrying the
  ¶72(a)–(g) minimum contents; Board approval is annual (¶71).
- ``cfp_activation_events``: the ¶74 activation/de-escalation log with the
  EWI snapshot and regulator-notification evidence.

No seed rows anywhere: starter definitions live in code, and every Board
trigger level or plan arrives through the audited endpoints.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608070036"
down_revision = "202608070035"
branch_labels = None
depends_on = None

# Post-platform-ID-epoch policy form (202607250027 precedent): organization_id
# is OR- text, so the policy compares text — never a ::uuid cast.
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

_TABLES = (
    "liquidity_ewi_indicators",
    "contingency_funding_plans",
    "cfp_activation_events",
)


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
        "liquidity_ewi_indicators",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("recovery_plan_reference", sa.String(length=200), nullable=True),
        sa.Column("watch_threshold", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("action_threshold", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("unit", sa.String(length=8), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("custom", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('above', 'below')",
            name="ck_liquidity_ewi_indicators_direction",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "code", name="uq_liquidity_ewi_indicators_scope"
        ),
    )
    op.create_index(
        "ix_liquidity_ewi_indicators_bank",
        "liquidity_ewi_indicators",
        ["organization_id", "bank_id"],
    )

    op.create_table(
        "contingency_funding_plans",
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
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'superseded')",
            name="ck_contingency_funding_plans_status",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "version", name="uq_contingency_funding_plans_version"
        ),
    )
    op.create_index(
        "ix_contingency_funding_plans_bank",
        "contingency_funding_plans",
        ["organization_id", "bank_id"],
    )

    op.create_table(
        "cfp_activation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cfp_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("ewi_snapshot", sa.JSON(), nullable=False),
        sa.Column("approval_overdue", sa.Boolean(), nullable=False),
        sa.Column("regulator_notification_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('activated', 'de_escalated')",
            name="ck_cfp_activation_events_type",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"]),
        sa.ForeignKeyConstraint(["cfp_id"], ["contingency_funding_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cfp_activation_events_bank",
        "cfp_activation_events",
        ["organization_id", "bank_id"],
    )

    for table in _TABLES:
        _force_rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
