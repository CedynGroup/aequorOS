"""Governed macro-scenario spine (docs/stress.md §3.1, Phase 1)

Revision ID: 202608190020
Revises: 202608160016

Two RLS-forced tenant tables introducing the governed, macro-variable-driven
scenario model (¶34, ¶38(e), AppIII Table 6):

- ``macro_scenarios`` — one governed scenario. ``organization_id`` scopes RLS;
  ``bank_id`` is nullable (NULL = a global-supervisory, org-wide scenario, the
  bottom-up-supervisory path of ¶13). Maker-checker lifecycle in ``status``
  (draft → pending_approval → approved → archived) with ``version``,
  ``created_by``/``approved_by``/``approval_timestamp``.
- ``macro_scenario_paths`` — the Table 6 macro paths ``(variable, year_index)``
  with a ``base_value`` and ``stress_value``; the translation layer maps their
  deltas onto the per-module shock vocabulary.

RLS policy form copies the post-platform-ID-epoch precedent (202608070032 /
202607250027): ``organization_id`` is OR- text, so the policy compares text with
no ``::uuid`` cast.

NOTE for the orchestrator (chain linearization): this revision keeps
``down_revision = "202608160016"`` as instructed — it is a fresh BRANCH off that
base, the same shape the repo already linearizes with merge revisions. At
authoring time the chain past ``202608160016`` had grown to::

    202608160016 ─┬─ 202608190017 (live_state) ────────┐
                  └─ 202608190018 (institution_types) ──┴─ 202608190019 (merge, head)

and this revision (``202608190020``) is a THIRD child of ``202608160016`` — so
after it lands there are TWO heads (``202608190019`` and ``202608190020``). The
revision id was moved off ``202608190018`` because that id was already taken by
``institution_types_registry`` (a duplicate id breaks alembic outright). To make
the chain single-headed, either add a merge revision over
(``202608190019``, ``202608190020``) — the pattern ``202608190019`` already uses —
or re-point this revision's ``down_revision`` to ``202608190019``. No data
migration is affected by either choice. The hermetic test suite builds the
schema with ``Base.metadata.create_all`` and is unaffected regardless.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608190020"
# Linearised by the integration pass: chained onto the merge 202608190019
# (which unifies 202608190017 live-state + 202608190018 institution_types) so
# the tree has a single head. Original branch-off point was 202608160016.
down_revision = "202608190019"
branch_labels = None
depends_on = None

_SCENARIOS = "macro_scenarios"
_PATHS = "macro_scenario_paths"
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

_SCENARIO_TYPES = "('base', 'adverse', 'historical', 'hypothetical', 'reverse', 'supervisory')"
_SEVERITIES = "('mild', 'moderate', 'severe')"
_STATUSES = "('draft', 'pending_approval', 'approved', 'archived')"
_VARIABLES = (
    "('gog_yield', 'gdp_growth', 'policy_rate', 'interest_rate', 'inflation', "
    "'unemployment', 'fx_usd_ghs', 'fx_gbp_ghs', 'fx_eur_ghs', 'gse_index', "
    "'fiscal_deficit', 'cocoa_price', 'gold_price')"
)


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
        _SCENARIOS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=True),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scenario_type", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("horizon_years", sa.Integer(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("institution_type_applicability", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"scenario_type IN {_SCENARIO_TYPES}", name="ck_macro_scenarios_scenario_type"
        ),
        sa.CheckConstraint(
            f"severity IS NULL OR severity IN {_SEVERITIES}",
            name="ck_macro_scenarios_severity",
        ),
        sa.CheckConstraint(f"status IN {_STATUSES}", name="ck_macro_scenarios_status"),
        sa.CheckConstraint("horizon_years >= 1", name="ck_macro_scenarios_horizon_years"),
        sa.CheckConstraint("version >= 1", name="ck_macro_scenarios_version"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "code", name="uq_macro_scenarios_scope"
        ),
    )
    op.create_index(
        "ix_macro_scenarios_org_bank_status",
        _SCENARIOS,
        ["organization_id", "bank_id", "status"],
    )
    op.create_index(
        "ix_macro_scenarios_org_type", _SCENARIOS, ["organization_id", "scenario_type"]
    )
    _enable_rls(_SCENARIOS)

    op.create_table(
        _PATHS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("variable", sa.String(length=40), nullable=False),
        sa.Column("year_index", sa.Integer(), nullable=False),
        sa.Column("base_value", sa.Numeric(precision=28, scale=6), nullable=False),
        sa.Column("stress_value", sa.Numeric(precision=28, scale=6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"variable IN {_VARIABLES}", name="ck_macro_scenario_paths_variable"
        ),
        sa.CheckConstraint("year_index >= 0", name="ck_macro_scenario_paths_year_index"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["macro_scenarios.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scenario_id", "variable", "year_index", name="uq_macro_scenario_paths_point"
        ),
    )
    op.create_index(
        "ix_macro_scenario_paths_scenario", _PATHS, ["organization_id", "scenario_id"]
    )
    _enable_rls(_PATHS)


def downgrade() -> None:
    _disable_rls(_PATHS)
    op.drop_index("ix_macro_scenario_paths_scenario", table_name=_PATHS)
    op.drop_table(_PATHS)
    _disable_rls(_SCENARIOS)
    op.drop_index("ix_macro_scenarios_org_type", table_name=_SCENARIOS)
    op.drop_index("ix_macro_scenarios_org_bank_status", table_name=_SCENARIOS)
    op.drop_table(_SCENARIOS)
