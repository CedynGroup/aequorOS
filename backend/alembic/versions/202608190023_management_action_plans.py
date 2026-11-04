"""Governed management-action plan library (docs/stress.md §3.7, Phase 3)

Revision ID: 202608190023
Revises: 202608190022

Two RLS-forced tenant tables introducing the governed management-actions plan
library the directive's Part IV requires (¶78–81): documented, credible,
triggered management actions modelled with-and-without in the enterprise stress
projection (AppII Table 1).

- ``management_action_plans`` — one governed plan. ``organization_id`` scopes RLS;
  ``bank_id`` is nullable (NULL = an org-wide default plan). Maker-checker
  lifecycle in ``status`` (draft → pending_approval → approved → archived) with
  ``version`` / ``created_by`` / ``approved_by`` / ``approval_timestamp`` — the
  same governance shape as ``macro_scenarios``.
- ``management_action_items`` — the credible actions in a plan; each row mirrors
  the pure ``ManagementAction`` contract one-for-one (kind, trigger, timeline,
  quantified effect, severity factors).

RLS policy form copies the macro-scenario precedent (202608190020): the
``organization_id`` OR- text is compared with no ``::uuid`` cast. The hermetic
test suite builds the schema with ``Base.metadata.create_all`` and never runs
this migration; it exists for the primary/production database.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608190023"
down_revision = "202608190022"
branch_labels = None
depends_on = None

_PLANS = "management_action_plans"
_ITEMS = "management_action_items"
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

_STATUSES = "('draft', 'pending_approval', 'approved', 'archived')"
_KINDS = (
    "('raise_capital', 'revise_dividend', 'reduce_risk', 'sell_assets', "
    "'change_strategy', 'other')"
)
_TIERS = "('cet1', 'at1', 'tier2')"
_SIZING = "('fixed', 'fill_residual')"
_TRIGGERS = "('always', 'on_breach', 'on_severity')"


def _enable_rls(table: str) -> None:
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


def _disable_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        _PLANS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=True),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"status IN {_STATUSES}", name="ck_mgmt_action_plans_status"),
        sa.CheckConstraint("version >= 1", name="ck_mgmt_action_plans_version"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "code", name="uq_mgmt_action_plans_scope"
        ),
    )
    op.create_index(
        "ix_mgmt_action_plans_org_bank_status",
        _PLANS,
        ["organization_id", "bank_id", "status"],
    )
    _enable_rls(_PLANS)

    op.create_table(
        _ITEMS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.String(length=60), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("trigger_kind", sa.String(length=20), nullable=False),
        sa.Column("watch_minima", sa.JSON(), nullable=True),
        sa.Column("min_severity", sa.String(length=20), nullable=True),
        sa.Column("effective_year", sa.Integer(), nullable=False),
        sa.Column("capital_raise_ghs", sa.Numeric(precision=28, scale=4), nullable=False),
        sa.Column("capital_raise_tier", sa.String(length=10), nullable=False),
        sa.Column("counts_as_paid_up", sa.Boolean(), nullable=False),
        sa.Column("sizing", sa.String(length=20), nullable=False),
        sa.Column("dividend_reduction_pct", sa.Numeric(precision=9, scale=4), nullable=False),
        sa.Column("rwa_reduction_ghs", sa.Numeric(precision=28, scale=4), nullable=False),
        sa.Column("shrinks_leverage_exposure", sa.Boolean(), nullable=False),
        sa.Column("severity_factors", sa.JSON(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"kind IN {_KINDS}", name="ck_mgmt_action_items_kind"),
        sa.CheckConstraint(
            f"capital_raise_tier IN {_TIERS}", name="ck_mgmt_action_items_tier"
        ),
        sa.CheckConstraint(f"sizing IN {_SIZING}", name="ck_mgmt_action_items_sizing"),
        sa.CheckConstraint(
            f"trigger_kind IN {_TRIGGERS}", name="ck_mgmt_action_items_trigger_kind"
        ),
        sa.CheckConstraint("effective_year >= 1", name="ck_mgmt_action_items_effective_year"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["management_action_plans.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "action_id", name="uq_mgmt_action_items_action"),
    )
    op.create_index("ix_mgmt_action_items_plan", _ITEMS, ["organization_id", "plan_id"])
    _enable_rls(_ITEMS)


def downgrade() -> None:
    _disable_rls(_ITEMS)
    op.drop_index("ix_mgmt_action_items_plan", table_name=_ITEMS)
    op.drop_table(_ITEMS)
    _disable_rls(_PLANS)
    op.drop_index("ix_mgmt_action_plans_org_bank_status", table_name=_PLANS)
    op.drop_table(_PLANS)
