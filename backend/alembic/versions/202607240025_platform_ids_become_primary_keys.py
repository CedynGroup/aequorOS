"""platform IDs become the primary keys for organizations and banks

Revision ID: 202607240025
Revises: 202607240024

One identity, no aliases: the short platform codes assigned in 202607240024
(OR-XXXXXXXX / BK-XXXXXXXX) are promoted to the PRIMARY KEYS of
``organizations`` and ``banks``. Every referencing column across the schema
converts from UUID to the code, all constraints/indexes/foreign keys are
recreated verbatim, and RLS policies are rewritten for text comparison
(``::uuid`` cast removed — the ``app.organization_id`` GUC now carries the
OR- code). Polymorphic ``entity_id`` columns (audit_events, notifications,
jobs) widen to text so they can hold either UUIDs or platform IDs.

The old UUIDs are archived in ``platform_id_legacy_map`` for forensic
traceability. This is a one-time identity epoch: input hashes of runs
recorded before this migration embed the old UUID string and remain
internally consistent with their stored snapshots; new runs hash the
platform ID.

Postgres-only: hermetic SQLite environments create the schema from the
models directly.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607240025"
down_revision = "202607240024"
branch_labels = None
depends_on = None

# Frozen from Base.metadata at authoring time (excludes the identity tables
# themselves; banks appears in ORG_TABLES for its organization_id column).
ORG_TABLES = [
    "jobs", "scenario_assumption_history", "stored_objects", "banks", "param_capital_threshold",
    "param_lcr_runoff_rate", "param_nsfr_weight", "param_risk_weight", "param_stress_shock",
    "sso_connections", "users", "audit_events", "bank_licenses", "bank_name_history",
    "bank_products", "bank_reporting_periods", "database_direct_connections",
    "institution_profiles", "mapping_configs", "market_data_connections",
    "market_data_quota_usage", "notifications", "outlets", "regulatory_channel_configs",
    "regulatory_packages", "regulatory_reporting_settings", "related_parties", "risk_cases",
    "temenos_connections", "bank_financial_facts", "documents", "financial_institutions",
    "financial_manual_edit_history", "financial_reporting_periods",
    "financial_validation_issues", "ingestion_batches", "live_findings", "live_metrics",
    "regulatory_package_approvals", "regulatory_package_artifacts",
    "regulatory_resubmission_requests", "regulatory_runs", "regulatory_submission_events",
    "related_party_roles", "risk_assessments", "risk_case_decisions", "risk_scenarios",
    "shareholdings", "calculation_runs", "document_chunks", "document_extractions",
    "financial_accounts", "lineage_records", "regulatory_line_items",
    "regulatory_metric_results", "regulatory_validations", "risk_assessment_runs",
    "scenario_assumptions", "translation_failures", "calculation_forecast_periods",
    "canonical_counterparties", "canonical_counterparty_ratings", "canonical_fx_rates",
    "canonical_gl_accounts", "canonical_market_indices", "canonical_positions",
    "canonical_products", "canonical_reference_rows", "canonical_yield_curves",
    "capital_projections", "financial_balances", "financial_cash_flows",
    "financial_obligations", "financial_source_rows", "liquidity_analysis_results",
    "risk_findings", "risk_scores", "canonical_position_snapshots",
    "canonical_yield_curve_points", "capital_indicators", "capital_projection_findings",
    "financial_covenants", "financial_record_source_links", "risk_finding_evidence",
]

BANK_TABLES = [
    "jobs", "bank_licenses", "bank_name_history", "bank_products", "bank_reporting_periods",
    "database_direct_connections", "institution_profiles", "mapping_configs",
    "market_data_connections", "market_data_quota_usage", "outlets",
    "regulatory_channel_configs", "regulatory_packages", "regulatory_reporting_settings",
    "related_parties", "temenos_connections", "bank_financial_facts", "ingestion_batches",
    "live_findings", "live_metrics", "regulatory_runs", "regulatory_line_items",
    "regulatory_metric_results", "regulatory_validations", "translation_failures",
    "canonical_counterparties", "canonical_counterparty_ratings", "canonical_fx_rates",
    "canonical_gl_accounts", "canonical_market_indices", "canonical_positions",
    "canonical_products", "canonical_reference_rows", "canonical_yield_curves",
    "canonical_position_snapshots", "canonical_yield_curve_points",
]

ENTITY_TEXT_COLUMNS = (
    ("audit_events", "entity_id"),
    ("notifications", "entity_id"),
    ("jobs", "entity_id"),
)

AFFECTED = tuple(dict.fromkeys([*ORG_TABLES, *BANK_TABLES, "organizations", "banks"]))


def _rows(connection, sql: str, **params):  # noqa: ANN001, ANN202
    return connection.execute(sa.text(sql), params).fetchall()


def upgrade() -> None:  # noqa: PLR0912, PLR0915 - one-time identity epoch, kept linear
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    affected_list = list(AFFECTED)

    # -- 1. Lift FORCE RLS for full owner visibility during the rewrite. ----
    forced = [
        r[0]
        for r in _rows(
            connection,
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relname = ANY(:tables) "
            "AND c.relforcerowsecurity",
            tables=affected_list,
        )
    ]
    for table in forced:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    # -- 2. Capture + drop RLS policies (their quals cast the GUC to uuid). --
    policies = _rows(
        connection,
        "SELECT tablename, policyname, permissive, roles, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname = current_schema() "
        "AND tablename = ANY(:tables)",
        tables=affected_list,
    )
    for table, name, *_ in policies:
        op.execute(f'DROP POLICY "{name}" ON {table}')

    # -- 3. Capture + drop every FK whose definition touches the converted
    #       columns or whose referenced table is an identity table (covers
    #       composite FKs like facts -> reporting_periods(org, bank)). -------
    fks = _rows(
        connection,
        "SELECT con.conname, con.conrelid::regclass::text AS tbl, "
        "       pg_get_constraintdef(con.oid) AS def "
        "FROM pg_constraint con "
        "JOIN pg_namespace n ON n.oid = con.connamespace "
        "WHERE n.nspname = current_schema() AND con.contype = 'f' AND ("
        "  con.confrelid IN ('organizations'::regclass, 'banks'::regclass) "
        "  OR pg_get_constraintdef(con.oid) ~ '\\m(organization_id|bank_id)\\M'"
        ")",
    )
    for name, table, _def in fks:
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"')

    # -- 4. Identity maps + permanent forensic archive. ----------------------
    op.execute(
        "CREATE TABLE platform_id_legacy_map ("
        "entity_type varchar(16) NOT NULL, "
        "legacy_uuid uuid NOT NULL, "
        "id varchar(16) NOT NULL, "
        "PRIMARY KEY (entity_type, legacy_uuid))"
    )
    op.execute(
        "INSERT INTO platform_id_legacy_map (entity_type, legacy_uuid, id) "
        "SELECT 'organization', id, public_id FROM organizations"
    )
    op.execute(
        "INSERT INTO platform_id_legacy_map (entity_type, legacy_uuid, id) "
        "SELECT 'bank', id, public_id FROM banks"
    )

    # -- 5. Convert referencing columns in ONE pass per table via
    #       ALTER TYPE ... USING CASE (identity counts are tiny, so the CASE
    #       stays small; indexes and unique constraints rebuild in place —
    #       critical for the multi-million-row canonical tables). -------------
    def case_expression(column: str, entity_type: str) -> str:
        pairs = _rows(
            connection,
            "SELECT legacy_uuid, id FROM platform_id_legacy_map "
            "WHERE entity_type = :entity",
            entity=entity_type,
        )
        whens = " ".join(
            f"WHEN '{legacy}'::uuid THEN '{code}'" for legacy, code in pairs
        )
        return f"CASE {column} {whens} END" if whens else "NULL"

    org_case = case_expression("organization_id", "organization")
    bank_case = case_expression("bank_id", "bank")

    org_set = set(ORG_TABLES)
    bank_set = set(BANK_TABLES)
    for table in dict.fromkeys([*ORG_TABLES, *BANK_TABLES]):
        clauses = []
        if table in org_set:
            clauses.append(
                f"ALTER COLUMN organization_id TYPE varchar(16) USING ({org_case})"
            )
        if table in bank_set:
            clauses.append(f"ALTER COLUMN bank_id TYPE varchar(16) USING ({bank_case})")
        op.execute(f"ALTER TABLE {table} " + ", ".join(clauses))

    # Promote the identity PKs (PK indexes rebuild in place) and retire the
    # now-redundant public_id columns.
    op.execute("ALTER TABLE organizations ALTER COLUMN id TYPE varchar(16) USING public_id")
    op.execute("ALTER TABLE organizations DROP COLUMN public_id")
    op.execute("ALTER TABLE banks ALTER COLUMN id TYPE varchar(16) USING public_id")
    op.execute("ALTER TABLE banks DROP COLUMN public_id")

    # -- 7. Polymorphic entity references widen to text. Operational rows
    #       (jobs, notifications) remap to the new identity so handlers and
    #       deep links keep resolving; audit_events stay as written — they
    #       are immutable history, and platform_id_legacy_map provides the
    #       join for forensic reads. ------------------------------------------
    for table, column in ENTITY_TEXT_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE varchar(40) "
            f"USING {column}::text"
        )
    for table in ("jobs", "notifications"):
        op.execute(
            f"UPDATE {table} SET entity_id = m.id FROM platform_id_legacy_map m "  # noqa: S608
            f"WHERE {table}.entity_id = m.legacy_uuid::text"
        )

    # -- 7. Recreate FKs and policies (indexes, PKs, and unique constraints
    #       survived the in-place ALTER TYPE). --------------------------------
    for name, table, definition in fks:
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT "{name}" {definition}')
    for table, name, permissive, roles, cmd, qual, with_check in policies:
        qual_text = (qual or "").replace("::uuid", "")
        check_text = (with_check or "").replace("::uuid", "")
        role_list = ", ".join(roles) if roles else "PUBLIC"
        statement = f'CREATE POLICY "{name}" ON {table}'
        statement += f" AS {'PERMISSIVE' if permissive == 'PERMISSIVE' else 'RESTRICTIVE'}"
        statement += f" FOR {cmd or 'ALL'} TO {role_list}"
        if qual_text:
            statement += f" USING ({qual_text})"
        if check_text:
            statement += f" WITH CHECK ({check_text})"
        op.execute(statement)

    # -- 9. Restore FORCE RLS exactly where it was. ---------------------------
    for table in forced:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    raise NotImplementedError(
        "202607240025 is a one-time identity epoch (UUID -> platform ID). "
        "The legacy UUIDs are preserved in platform_id_legacy_map; restore "
        "from backup to roll back."
    )
