"""Enterprise-stress sign-off / Board attestation (docs/stress.md §3.8, Phase 5)

Revision ID: 202608190024
Revises: 202608190023

One RLS-forced tenant table introducing the stress-run governance record the
directive's Part II requires (¶10–29, ¶20, ¶57–63): the Board attests it has
reviewed and challenged both the framework and the results, with a rationale for
their credibility. Each row binds one immutable enterprise-stress
``RegulatoryRun`` to that governance record and carries the analyst/CRO narrative
& assumptions rationale the annual ICAAP submission requires (¶67(b)(c)).

- ``enterprise_stress_signoffs`` — ``organization_id`` scopes RLS; the
  ``(run_id, organization_id, bank_id)`` composite FK ties the sign-off to a run
  within the same tenant + bank (mirroring ``regulatory_metric_results``).
  Maker-checker lifecycle in ``status`` (draft → pending_attestation → attested,
  with ``withdrawn``); only an ``attested`` sign-off makes the run eligible to
  feed the ICAAP Appendix II submission.

RLS policy form copies the macro-scenario precedent (202608190020): the
``organization_id`` OR- text is compared with no ``::uuid`` cast. The hermetic
test suite builds the schema with ``Base.metadata.create_all`` and never runs
this migration; it exists for the primary/production database.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608190024"
down_revision = "202608190023"
branch_labels = None
depends_on = None

_SIGNOFFS = "enterprise_stress_signoffs"
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

_STATUSES = "('draft', 'pending_attestation', 'attested', 'withdrawn')"


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
        _SIGNOFFS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("reporting_period_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_code", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scenario_narrative", sa.Text(), nullable=False),
        sa.Column("assumptions_rationale", sa.Text(), nullable=False),
        sa.Column("methodology_summary", sa.Text(), nullable=True),
        sa.Column("board_challenge", sa.Text(), nullable=True),
        sa.Column("credibility_rationale", sa.Text(), nullable=True),
        sa.Column("stays_above_all_minima", sa.Boolean(), nullable=True),
        sa.Column("with_actions_stays_above_all_minima", sa.Boolean(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("submitted_by", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attested_by", sa.Uuid(), nullable=True),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN {_STATUSES}", name="ck_enterprise_stress_signoffs_status"
        ),
        sa.CheckConstraint("version >= 1", name="ck_enterprise_stress_signoffs_version"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["run_id", "organization_id", "bank_id"],
            [
                "regulatory_runs.id",
                "regulatory_runs.organization_id",
                "regulatory_runs.bank_id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "run_id", name="uq_enterprise_stress_signoffs_run"
        ),
    )
    op.create_index(
        "ix_enterprise_stress_signoffs_scope",
        _SIGNOFFS,
        ["organization_id", "bank_id", "reporting_period_id", "status"],
    )
    _enable_rls(_SIGNOFFS)


def downgrade() -> None:
    _disable_rls(_SIGNOFFS)
    op.drop_index("ix_enterprise_stress_signoffs_scope", table_name=_SIGNOFFS)
    op.drop_table(_SIGNOFFS)
