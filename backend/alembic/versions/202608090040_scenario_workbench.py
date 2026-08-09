"""Scenario Workbench: desk stress scenarios + saved analyses

Revision ID: 202608090040
Revises: 202608070039

Two RLS-forced tenant tables for the treasury sandbox layer:

- ``stress_scenarios``: user-authored scenarios per module (system/regulatory
  scenarios stay code-defined + ParamStressShock-priced, never rows here).
- ``scenario_analyses``: optionally saved what-if results. Deliberately
  outside the regulatory spine — never a RegulatoryRun, never read by report
  generation.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608090040"
down_revision = "202608070039"
branch_labels = None
depends_on = None

# Post-platform-ID-epoch policy form: text comparison, never a ::uuid cast.
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"
_TABLES = ("stress_scenarios", "scenario_analyses")
_MODULES = "('liquidity', 'capital', 'irr', 'fx', 'ftp')"


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
        "stress_scenarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("module", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("shocks", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"module IN {_MODULES}", name="ck_stress_scenarios_module"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "module", "code", name="uq_stress_scenarios_scope"
        ),
    )
    op.create_index(
        "ix_stress_scenarios_bank",
        "stress_scenarios",
        ["organization_id", "bank_id", "module"],
    )

    op.create_table(
        "scenario_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("module", sa.String(length=16), nullable=False),
        sa.Column("reporting_period_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("engine_version", sa.String(length=60), nullable=False),
        sa.Column("scenarios", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"module IN {_MODULES}", name="ck_scenario_analyses_module"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scenario_analyses_bank",
        "scenario_analyses",
        ["organization_id", "bank_id", "module", "reporting_period_id"],
    )

    for table in _TABLES:
        _force_rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
