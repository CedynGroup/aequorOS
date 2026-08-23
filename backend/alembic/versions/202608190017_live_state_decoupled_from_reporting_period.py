"""Decouple operational live state from regulatory reporting periods.

Revision ID: 202608190017
Revises: 202608160016
Create Date: 2026-08-19 12:00:00.000000

``live_metrics`` and ``live_findings`` used to be unique per reporting period.
That made a governance selection an accidental prerequisite for current ALM
state. Existing rows are preserved and their period becomes nullable provenance
(``source_fact_period_id``); current identity is now organisation/bank/module.
No regulatory run, package, or historical fact is changed by this migration.
"""

import sqlalchemy as sa

from alembic import op

revision = "202608190017"
down_revision = "202608160016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    rls_states: dict[str, tuple[bool, bool]] = {}
    if connection.dialect.name == "postgresql":
        for table in ("live_metrics", "live_findings"):
            state = connection.execute(
                sa.text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    f"WHERE oid = '{table}'::regclass"
                )
            ).one()
            rls_states[table] = (state.relrowsecurity, state.relforcerowsecurity)
            if state.relrowsecurity:
                op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    with op.batch_alter_table("live_metrics") as batch:
        batch.drop_constraint("uq_live_metrics_org_bank_period_module", type_="unique")
        batch.alter_column(
            "reporting_period_id", new_column_name="source_fact_period_id", nullable=True
        )
        batch.add_column(sa.Column("source_as_of_date", sa.Date(), nullable=True))
        batch.add_column(
            sa.Column(
                "engine_version", sa.String(length=80), nullable=False, server_default="unknown"
            )
        )
        batch.add_column(
            sa.Column("calculation_generation", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "pipeline_state", sa.String(length=16), nullable=False, server_default="ready"
            )
        )
        batch.add_column(sa.Column("pipeline_error", sa.Text(), nullable=True))
    # Historical rows were unique per period; retain only the newest cache row
    # for each new live identity before adding the tighter uniqueness rule.
    op.execute(
        "DELETE FROM live_metrics WHERE id IN ("
        "SELECT id FROM (SELECT id, row_number() OVER ("
        "PARTITION BY organization_id, bank_id, module "
        "ORDER BY computed_at DESC, updated_at DESC, created_at DESC) AS rank "
        "FROM live_metrics) duplicates WHERE rank > 1)"
    )
    op.create_unique_constraint(
        "uq_live_metrics_org_bank_module",
        "live_metrics",
        ["organization_id", "bank_id", "module"],
    )
    op.drop_index("ix_live_metrics_org_bank_period", table_name="live_metrics")
    op.create_index("ix_live_metrics_org_bank", "live_metrics", ["organization_id", "bank_id"])
    op.execute(
        "UPDATE live_metrics SET source_as_of_date = bank_reporting_periods.period_end "
        "FROM bank_reporting_periods "
        "WHERE live_metrics.source_fact_period_id = bank_reporting_periods.id"
    )
    # Some legacy cache rows may not see their old period through RLS during a
    # migration. The computation timestamp is an honest fallback provenance;
    # live cache is operational state, not regulatory evidence.
    op.execute(
        "UPDATE live_metrics SET source_as_of_date = computed_at::date "
        "WHERE source_as_of_date IS NULL"
    )
    with op.batch_alter_table("live_metrics") as batch:
        batch.alter_column("source_as_of_date", nullable=False)

    op.drop_index("uq_live_findings_open", table_name="live_findings")
    with op.batch_alter_table("live_findings") as batch:
        batch.alter_column(
            "reporting_period_id", new_column_name="source_fact_period_id", nullable=True
        )
        batch.add_column(sa.Column("source_as_of_date", sa.Date(), nullable=True))
    op.execute(
        "UPDATE live_findings SET source_as_of_date = bank_reporting_periods.period_end "
        "FROM bank_reporting_periods "
        "WHERE live_findings.source_fact_period_id = bank_reporting_periods.id"
    )
    op.execute(
        "UPDATE live_findings SET source_as_of_date = created_at::date "
        "WHERE source_as_of_date IS NULL"
    )
    with op.batch_alter_table("live_findings") as batch:
        batch.alter_column("source_as_of_date", nullable=False)
    # Continuing historical breaches can exist once per old period. Retain the
    # newest open row; older rows are obsolete cache state, not audit evidence.
    op.execute(
        "DELETE FROM live_findings WHERE id IN ("
        "SELECT id FROM (SELECT id, row_number() OVER ("
        "PARTITION BY organization_id, bank_id, module, rule_id "
        "ORDER BY updated_at DESC, created_at DESC) AS rank "
        "FROM live_findings WHERE status = 'open') duplicates WHERE rank > 1)"
    )
    op.create_index(
        "uq_live_findings_open",
        "live_findings",
        ["organization_id", "bank_id", "module", "rule_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
        sqlite_where=sa.text("status = 'open'"),
    )
    if connection.dialect.name == "postgresql":
        for table, (enabled, forced) in rls_states.items():
            if enabled:
                op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                if forced:
                    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("uq_live_findings_open", table_name="live_findings")
    with op.batch_alter_table("live_findings") as batch:
        batch.drop_column("source_as_of_date")
        batch.alter_column(
            "source_fact_period_id", new_column_name="reporting_period_id", nullable=False
        )
    op.create_index(
        "uq_live_findings_open",
        "live_findings",
        ["organization_id", "bank_id", "reporting_period_id", "module", "rule_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
        sqlite_where=sa.text("status = 'open'"),
    )

    op.drop_index("ix_live_metrics_org_bank", table_name="live_metrics")
    with op.batch_alter_table("live_metrics") as batch:
        batch.drop_constraint("uq_live_metrics_org_bank_module", type_="unique")
        batch.drop_column("pipeline_error")
        batch.drop_column("pipeline_state")
        batch.drop_column("calculation_generation")
        batch.drop_column("engine_version")
        batch.drop_column("source_as_of_date")
        batch.alter_column(
            "source_fact_period_id", new_column_name="reporting_period_id", nullable=False
        )
        batch.create_unique_constraint(
            "uq_live_metrics_org_bank_period_module",
            ["organization_id", "bank_id", "reporting_period_id", "module"],
        )
    op.create_index(
        "ix_live_metrics_org_bank_period",
        "live_metrics",
        ["organization_id", "bank_id", "reporting_period_id"],
    )