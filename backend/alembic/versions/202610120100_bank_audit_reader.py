"""Officer bundles and tenant/bank-scoped audit verification reads.

Revision ID: 202610120100
Revises: 202610120099

No bindings are backfilled: a bank must explicitly assign its officers.
"""

from typing import cast

import sqlalchemy as sa

from alembic import op

revision = "202610120100"
down_revision = "202610120099"
branch_labels = None
depends_on = None

# Frozen at this revision: later application ownership changes must not alter
# historical policies, definer functions, or the tables granted to the audit owner.
_SUBJECT_TABLES = {
    "ingestion_batch": "ingestion_batches",
    "mapping_config": "mapping_configs",
    "canonical_position_snapshot": "canonical_position_snapshots",
    "regulatory_run": "regulatory_runs",
    "regulatory_package": "regulatory_packages",
    "regulatory_package_artifact": "regulatory_package_artifacts",
    "regulatory_artifact_version": "regulatory_artifact_versions",
    "database_direct_connection": "database_direct_connections",
    "temenos_connection": "temenos_connections",
    "market_data_connection": "market_data_connections",
    "filing_workflow_template": "filing_workflow_templates",
    "capital_plan": "capital_plans",
    "ilaap_snapshot": "ilaap_snapshots",
    "contingency_funding_plan": "contingency_funding_plans",
    "stress_scenario": "stress_scenarios",
    "scenario_analysis": "scenario_analyses",
    "macro_scenario": "macro_scenarios",
    "management_action_plan": "management_action_plans",
    "enterprise_stress_signoff": "enterprise_stress_signoffs",
    "authorization_access_request": "authorization_access_requests",
    "authorization_binding": "authorization_bindings",
    "bank_supervisory_addon": "bank_supervisory_addons",
    "system_of_record_declaration": "system_of_record_declarations",
    "canonical_withdrawal": "canonical_withdrawals",
    "market_data_overlay": "market_data_overlays",
    "market_data_source_preference": "market_data_source_preferences",
    "regulatory_channel_config": "regulatory_channel_configs",
    "regulatory_reporting_settings": "regulatory_reporting_settings",
    "icaap_risk_assessment": "icaap_risk_assessments",
    "icaap_appetite_metric": "icaap_appetite_metrics",
    "icaap_pillar2_item": "icaap_pillar2_items",
    "icaap_pillar2_item_revision": "icaap_pillar2_item_revisions",
    "icaap_capital_allocation": "icaap_capital_allocations",
    "icaap_requirement_reconciliation_line": "icaap_requirement_reconciliation_lines",
    "icaap_resources_reconciliation_line": "icaap_resources_reconciliation_lines",
    "icaap_control_explanation": "icaap_control_explanations",
    "icaap_audit_review": "icaap_audit_reviews",
    "icaap_challenge": "icaap_challenges",
    "icaap_challenge_response": "icaap_challenge_responses",
    "icaap_ai_suggestion": "icaap_ai_suggestions",
    "icaap_ai_suggestion_decision": "icaap_ai_suggestion_decisions",
    "icaap_cycle": "icaap_cycles",
    "icaap_section": "icaap_sections",
    "icaap_section_version": "icaap_section_versions",
    "icaap_data_block": "icaap_data_blocks",
    "icaap_attachment": "icaap_attachments",
    "icaap_block_binding": "icaap_block_bindings",
    "icaap_attachment_withdrawal": "icaap_attachment_withdrawals",
    "icaap_workflow_template": "icaap_workflow_templates",
    "icaap_cycle_stage": "icaap_cycle_stages",
    "icaap_stage_decision": "icaap_stage_decisions",
    "icaap_disclosure": "icaap_disclosures",
    "push_batch": "audit_events",
    "param_ecl_assumption": "banks",
    "param_crm_haircut": "banks",
    "param_concentration_limit": "banks",
    "param_credit_threshold": "banks",
    "param_liquidity_threshold": "banks",
    "param_liquidity_haircut": "banks",
}

_PACKAGE_SUBJECTS = {"regulatory_package_artifact", "regulatory_artifact_version"}

_SHARED_REGISTER_SUBJECTS = frozenset(
    kind for kind, table in _SUBJECT_TABLES.items() if table == "banks"
)


def _shared_register_sql(
    organization: str, entity_type: str, entity_id: str, details: str, bank: str
) -> str:
    kinds = ", ".join(f"'{kind}'" for kind in sorted(_SHARED_REGISTER_SUBJECTS))
    return (
        f"({entity_type} IN ({kinds}) AND EXISTS ("
        "SELECT 1 FROM banks origin JOIN banks affected"
        " ON affected.organization_id = origin.organization_id"
        " AND affected.jurisdiction_code = origin.jurisdiction_code"
        f" WHERE origin.organization_id = {organization} AND origin.id = {entity_id}"
        f" AND affected.id = {bank}"
        f" AND ({details}->>'bank_id' IS NULL OR {details}->>'bank_id' = origin.id)"
        f" AND ({details}->'scope'->>'institution_id' IS NULL"
        f" OR {details}->'scope'->>'institution_id' = origin.id)"
        f" AND ({entity_type} <> 'param_liquidity_threshold' OR EXISTS ("
        "SELECT 1 FROM institution_types origin_type JOIN institution_types affected_type"
        " ON affected_type.institution_class = origin_type.institution_class"
        " WHERE origin_type.type_code = origin.institution_type"
        " AND affected_type.type_code = affected.institution_type"
        f" AND ({details}->>'institution_class' IS NULL"
        f" OR {details}->>'institution_class' = origin_type.institution_class)))"
        "))"
    )


def _uuid_sql(value: str, *, postgresql: bool) -> str:
    return (
        f"CASE WHEN {value} ~ '^[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-"
        f"[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}}$'"
        f" THEN CAST({value} AS uuid) END"
        if postgresql
        else f"lower(replace({value}, '-', ''))"
    )


def _subject_bank_sql(  # noqa: PLR0913
    organization: str,
    entity_type: str,
    entity_id: str,
    *,
    postgresql: bool,
    subject: str | None = None,
    details: str | None = None,
) -> str:
    identifier = _uuid_sql(entity_id, postgresql=postgresql)
    queries: list[str] = []
    for kind, table in _SUBJECT_TABLES.items():
        if subject is not None and kind != subject:
            continue
        if kind == "push_batch":
            batch_id = _uuid_sql("s.details->>'ingestion_batch_id'", postgresql=postgresql)
            queries.append(
                "SELECT DISTINCT b.bank_id FROM audit_events s JOIN ingestion_batches b"
                f" ON b.id = {batch_id} AND b.organization_id = s.organization_id"
                f" WHERE {entity_type} = 'push_batch' AND s.entity_type = 'push_batch'"
                f" AND s.entity_id = {entity_id} AND s.organization_id = {organization}"
                " AND s.event_type = 'push_batch.committed'"
            )
            continue
        owner = "p" if kind in _PACKAGE_SUBJECTS else "s"
        bank_column = (
            "s.id"
            if table == "banks"
            else "s.institution_id"
            if kind in {"authorization_access_request", "authorization_binding"}
            else f"{owner}.bank_id"
        )
        subject_id = entity_id if table == "banks" else identifier
        join = (
            " JOIN regulatory_packages p ON p.id = s.package_id"
            " AND p.organization_id = s.organization_id"
            if kind in _PACKAGE_SUBJECTS
            else ""
        )
        queries.append(
            f"SELECT {bank_column} AS bank_id FROM {table} s{join}"
            f" WHERE {entity_type} = '{kind}' AND s.organization_id = {organization}"
            f" AND s.id = {subject_id}"
        )
        if details is not None and kind.startswith("icaap_") and kind != "icaap_cycle":
            cycle_id = _uuid_sql(f"{details}->>'cycle_id'", postgresql=postgresql)
            queries.append(
                f"SELECT c.bank_id FROM icaap_cycles c WHERE {entity_type} = '{kind}'"
                f" AND c.organization_id = {organization} AND c.id = {cycle_id}"
                f" AND NOT EXISTS (SELECT 1 FROM {table} s"
                f" WHERE s.id = {identifier} AND s.organization_id = {organization})"
            )
    return "(SELECT bank_id FROM (" + " UNION ALL ".join(queries) + ") audit_subject_owner)"


def _staff_bank_sql(organization: str, detail: str, *, postgresql: bool) -> str:
    """Retained ownership for historical batch and mapping staff operations."""
    batch = _subject_bank_sql(
        organization,
        "'ingestion_batch'",
        f"{detail}->>'batch_id'",
        postgresql=postgresql,
        subject="ingestion_batch",
    )
    mapping = _subject_bank_sql(
        organization,
        "'mapping_config'",
        f"{detail}->>'target_id'",
        postgresql=postgresql,
        subject="mapping_config",
    )
    return (
        f"COALESCE({batch}, CASE WHEN {detail}->>'kind' = 'mapping_active'"
        f" THEN {mapping} END, {detail}->'after'->>'bank_id')"
    )


def _organization_binding_sql(
    organization: str, entity_type: str, entity_id: str, *, postgresql: bool
) -> str:
    identifier = _uuid_sql(entity_id, postgresql=postgresql)
    return (
        f"({entity_type} = 'authorization_binding' AND EXISTS ("
        "SELECT 1 FROM authorization_bindings s"
        f" WHERE s.organization_id = {organization} AND s.id = {identifier}"
        " AND s.institution_scope = 'organization' AND s.institution_id IS NULL))"
    )


_PREVIOUS_ROLE_BUNDLES = (
    "'member','viewer','auditor','analyst','approver','validator',"
    "'account_admin','org_owner','integration_writer','bi_reader'"
)
_SESSION_BANK_ID = "current_setting('app.audit_bank_id', true)"
_SESSION_ORGANIZATION_ID = "current_setting('app.organization_id', true)"
_SUBJECT_BANK_ID = _subject_bank_sql(
    "e.organization_id", "e.entity_type", "e.entity_id", postgresql=True, details="e.details"
)
_ORGANIZATION_WIDE_BINDING = _organization_binding_sql(
    "e.organization_id", "e.entity_type", "e.entity_id", postgresql=True
)
# Admit explicit or retained bank ownership, including organization-wide bindings.
# The following AND guards reject conflicting bank references, rather than letting
# one matching reference override another bank's ownership.
_SHARED_REGISTER_EVENT = _shared_register_sql(
    "e.organization_id", "e.entity_type", "e.entity_id", "e.details", _SESSION_BANK_ID
)
_ATTRIBUTABLE_TENANT_EVENT = f"""
    e.organization_id = {_SESSION_ORGANIZATION_ID}
    AND ({_SHARED_REGISTER_EVENT} OR (

    (
        e.details ->> 'bank_id' = {_SESSION_BANK_ID}
        OR (e.entity_type = 'bank' AND e.entity_id = {_SESSION_BANK_ID})
        OR e.details -> 'scope' ->> 'institution_id' = {_SESSION_BANK_ID}
        OR (
            e.entity_type = 'authorization_access_request'
            AND e.details ->> 'institution_id' = {_SESSION_BANK_ID}
        )
        OR {_SUBJECT_BANK_ID} = {_SESSION_BANK_ID}
        OR {_ORGANIZATION_WIDE_BINDING}
    )
    AND (e.details ->> 'bank_id' IS NULL OR e.details ->> 'bank_id' = {_SESSION_BANK_ID})
    AND (
        e.details -> 'scope' ->> 'institution_id' IS NULL
        OR e.details -> 'scope' ->> 'institution_id' = {_SESSION_BANK_ID}
    )
    AND (e.entity_type IS DISTINCT FROM 'bank' OR e.entity_id = {_SESSION_BANK_ID})
    AND ({_SUBJECT_BANK_ID} IS NULL OR {_SUBJECT_BANK_ID} = {_SESSION_BANK_ID})
    AND (
        e.entity_type IS DISTINCT FROM 'authorization_access_request'
        OR e.details ->> 'institution_id' IS NULL
        OR e.details ->> 'institution_id' = {_SESSION_BANK_ID}
    )

    ))
"""

_STAFF_SUBJECT_BANK_ID = _staff_bank_sql("e.target_org", "e.detail", postgresql=True)
# Staff records require this tenant and bank, with no conflicting bank references.
_ATTRIBUTABLE_STAFF_EVENT = f"""
    e.target_org = {_SESSION_ORGANIZATION_ID}
    AND (
        e.detail ->> 'bank_id' = {_SESSION_BANK_ID}
        OR {_STAFF_SUBJECT_BANK_ID} = {_SESSION_BANK_ID}
    )
    AND (e.detail ->> 'bank_id' IS NULL OR e.detail ->> 'bank_id' = {_SESSION_BANK_ID})
    AND ({_STAFF_SUBJECT_BANK_ID} IS NULL OR {_STAFF_SUBJECT_BANK_ID} = {_SESSION_BANK_ID})
    AND (
        e.detail -> 'after' ->> 'bank_id' IS NULL
        OR e.detail -> 'after' ->> 'bank_id' = {_SESSION_BANK_ID}
    )
"""


_ATTRIBUTABLE_STAFF_POLICY = _ATTRIBUTABLE_STAFF_EVENT.replace("e.", "operator_audit_log.")

_OWNERSHIP_TABLES = sorted(
    (set(_SUBJECT_TABLES.values()) - {"audit_events"}) | {"institution_types"}
)


def upgrade() -> None:
    # Schema and officer bundles. No officer bindings are backfilled.
    op.add_column(
        "operator_inspector_sessions", sa.Column("support_reference", sa.String(200), nullable=True)
    )
    op.create_index(
        "ix_audit_events_subject",
        "audit_events",
        ["organization_id", "entity_id"],
    )
    op.drop_index("ix_audit_events_organization_id", table_name="audit_events")
    op.drop_constraint(
        "ck_authorization_bindings_role_bundle", "authorization_bindings", type_="check"
    )
    op.create_check_constraint(
        "ck_authorization_bindings_role_bundle",
        "authorization_bindings",
        f"role_bundle IN ({_PREVIOUS_ROLE_BUNDLES},'security_officer','compliance_officer')",
    )
    op.create_check_constraint(
        "ck_authorization_bindings_audit_officer_scope",
        "authorization_bindings",
        "role_bundle NOT IN ('security_officer', 'compliance_officer') OR "
        "(module_scope = 'audit' AND data_scope_kind = 'all')",
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    # PostgreSQL ownership reads and staff/chain visibility policies.
    schema = cast(str, op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    quoted = op.get_bind().dialect.identifier_preparer.quote_identifier(schema)
    op.execute(f"GRANT SELECT ON {', '.join(_OWNERSHIP_TABLES)} TO aequoros_audit_owner")
    op.execute(f"""
        ALTER TABLE operator_audit_log ENABLE ROW LEVEL SECURITY;
        ALTER TABLE operator_audit_log FORCE ROW LEVEL SECURITY;
        CREATE POLICY bank_audit_staff_owner ON operator_audit_log TO aequoros_audit_owner
        USING (true) WITH CHECK (true);
        CREATE POLICY bank_audit_staff_insert ON operator_audit_log FOR INSERT WITH CHECK (
            true
        );
        CREATE POLICY bank_audit_staff_verifier ON operator_audit_log FOR SELECT
        TO aqs_audit_verifier, aqs_operator USING (true);
        CREATE POLICY bank_audit_staff_read ON operator_audit_log FOR SELECT USING (
            {_ATTRIBUTABLE_STAFF_POLICY}
        );
        CREATE POLICY bank_audit_entries_read ON audit_chain_entries FOR SELECT USING (
            (stream = 'audit_events' AND EXISTS (
                SELECT 1
                FROM audit_events e
                WHERE e.id = audit_chain_entries.event_id AND {_ATTRIBUTABLE_TENANT_EVENT}
            ))
            OR (stream = 'operator_audit_log' AND EXISTS (
                SELECT 1
                FROM operator_audit_log e
                WHERE e.id = audit_chain_entries.event_id AND {_ATTRIBUTABLE_STAFF_EVENT}
            ))
        );
    """)
    # Attributable-event predecessor verification.
    # The definer returns a boolean for an attributable event, never another
    # tenant's source row or the preceding global entry's payload/hash.
    op.execute(f"""
        CREATE FUNCTION bank_audit_previous_link(
            wanted_stream text, wanted_sequence bigint, wanted_hash text
        )
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, {quoted}, pg_temp AS $fn$
            SELECT EXISTS(
                SELECT 1
                FROM audit_chain_entries c
                WHERE
                    c.stream = wanted_stream
                    AND c.sequence = wanted_sequence
                    AND c.previous_hash = wanted_hash
                    AND (
                        (c.stream = 'audit_events' AND EXISTS (
                            SELECT 1
                            FROM audit_events e
                            WHERE e.id = c.event_id AND {_ATTRIBUTABLE_TENANT_EVENT}
                        ))
                        OR (c.stream = 'operator_audit_log' AND EXISTS (
                            SELECT 1
                            FROM operator_audit_log e
                            WHERE e.id = c.event_id AND {_ATTRIBUTABLE_STAFF_EVENT}
                        ))
                    )
                    AND c.previous_hash = CASE
                        WHEN c.sequence = 1 THEN repeat('0', 64)
                        ELSE (
                            SELECT p.entry_hash
                            FROM audit_chain_entries p
                            WHERE p.stream = c.stream AND p.sequence = c.sequence - 1
                        )
                    END
            )
        $fn$;
    """)
    # Session purpose/reference evidence for the attributable event only.
    op.execute(f"""
        CREATE FUNCTION bank_audit_inspection_context(
            wanted_stream text, wanted_event uuid
        )
        RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, {quoted}, pg_temp AS $fn$
            WITH evidence AS (
                SELECT
                    e.organization_id,
                    e.details ->> 'act_operator' AS operator_email,
                    e.details ->> 'impersonation_session_id' AS session_id
                FROM audit_events e
                WHERE
                    wanted_stream = 'audit_events'
                    AND e.id = wanted_event AND {_ATTRIBUTABLE_TENANT_EVENT}
                    AND (
                        e.entity_type IS NULL OR e.entity_type NOT IN (
                            {", ".join(f"'{kind}'" for kind in sorted(_SHARED_REGISTER_SUBJECTS))}
                        ) OR e.entity_id = {_SESSION_BANK_ID}
                    )
                UNION ALL
                SELECT
                    e.target_org,
                    e.operator_email,
                    e.detail ->> 'session_id'
                FROM operator_audit_log e
                WHERE
                    wanted_stream = 'operator_audit_log'
                    AND e.id = wanted_event AND {_ATTRIBUTABLE_STAFF_EVENT}
            )

            SELECT
                jsonb_build_object(
                    'reason', s.reason, 'support_reference', s.support_reference
                )
            FROM evidence e
            JOIN operator_inspector_sessions s
                ON
                    s.id = {_uuid_sql("e.session_id", postgresql=True)}
                    AND s.organization_id = e.organization_id
                    AND s.started_by = e.operator_email
        $fn$;
    """)
    # Transfer function custody, then remove temporary schema creation authority.
    op.execute("GRANT SELECT ON operator_inspector_sessions TO aequoros_audit_owner")
    # PostgreSQL requires the new function owner to have schema CREATE during
    # ownership transfer. The audit owner retains custody, never schema CREATE.
    op.execute(f"GRANT CREATE ON SCHEMA {quoted} TO aequoros_audit_owner")
    op.execute(
        "ALTER FUNCTION bank_audit_previous_link(text,bigint,text) OWNER TO aequoros_audit_owner"
    )
    op.execute(
        "ALTER FUNCTION bank_audit_inspection_context(text,uuid) OWNER TO aequoros_audit_owner"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA {quoted} FROM aequoros_audit_owner")
    # Restrict execution to existing nonsuperuser chain readers, including aqs_api.
    op.execute("""
        REVOKE ALL ON FUNCTION bank_audit_previous_link(text, bigint, text) FROM PUBLIC;
        REVOKE ALL ON FUNCTION bank_audit_inspection_context(text, uuid) FROM PUBLIC;
        GRANT SELECT ON operator_audit_log, audit_chain_entries TO aqs_api;
        DO $$
        DECLARE
            r record;
        BEGIN
            FOR r IN
                SELECT rolname
                FROM pg_roles
                WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
                AND has_table_privilege(rolname, 'audit_chain_entries', 'SELECT')
            LOOP
                EXECUTE format('GRANT EXECUTE ON FUNCTION '
                    'bank_audit_previous_link(text,bigint,text) TO %I', r.rolname);
                EXECUTE format('GRANT EXECUTE ON FUNCTION '
                    'bank_audit_inspection_context(text,uuid) TO %I', r.rolname);
            END LOOP;
        END
        $$;
    """)


def downgrade() -> None:
    # Restore the older organization lookup before removing its replacement.
    op.create_index("ix_audit_events_organization_id", "audit_events", ["organization_id"])
    # Preserve the historical removal order before restoring the older role CHECK.
    op.drop_index("ix_audit_events_subject", table_name="audit_events")
    op.drop_constraint(
        "ck_authorization_bindings_audit_officer_scope", "authorization_bindings", type_="check"
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION bank_audit_inspection_context(text,uuid)")
        op.execute("REVOKE SELECT ON operator_inspector_sessions FROM aequoros_audit_owner")
        op.execute("DROP FUNCTION bank_audit_previous_link(text,bigint,text)")
        op.execute("DROP POLICY bank_audit_entries_read ON audit_chain_entries")
        for name in ("owner", "insert", "verifier", "read"):
            op.execute(f"DROP POLICY bank_audit_staff_{name} ON operator_audit_log")
        op.execute("ALTER TABLE operator_audit_log NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE operator_audit_log DISABLE ROW LEVEL SECURITY")
        op.execute("REVOKE SELECT ON operator_audit_log, audit_chain_entries FROM aqs_api")
        op.execute(f"REVOKE SELECT ON {', '.join(_OWNERSHIP_TABLES)} FROM aequoros_audit_owner")
    op.drop_column("operator_inspector_sessions", "support_reference")
    op.drop_constraint(
        "ck_authorization_bindings_role_bundle", "authorization_bindings", type_="check"
    )
    op.create_check_constraint(
        "ck_authorization_bindings_role_bundle",
        "authorization_bindings",
        f"role_bundle IN ({_PREVIOUS_ROLE_BUNDLES})",
    )
