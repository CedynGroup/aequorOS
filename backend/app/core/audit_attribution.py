"""Bank ownership of retained Data Engine and filing audit subjects."""

SUBJECT_TABLES = {
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

PACKAGE_SUBJECTS = {"regulatory_package_artifact", "regulatory_artifact_version"}


def uuid_sql(value: str, *, postgresql: bool) -> str:
    return (
        f"CASE WHEN {value} ~ '^[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-"
        f"[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}}$'"
        f" THEN CAST({value} AS uuid) END"
        if postgresql
        else f"lower(replace({value}, '-', ''))"
    )


def subject_bank_sql(  # noqa: PLR0913
    organization: str,
    entity_type: str,
    entity_id: str,
    *,
    postgresql: bool,
    subject: str | None = None,
    details: str | None = None,
) -> str:
    identifier = uuid_sql(entity_id, postgresql=postgresql)
    queries: list[str] = []
    for kind, table in SUBJECT_TABLES.items():
        if subject is not None and kind != subject:
            continue
        if kind == "push_batch":
            batch_id = uuid_sql("s.details->>'ingestion_batch_id'", postgresql=postgresql)
            queries.append(
                "SELECT DISTINCT b.bank_id FROM audit_events s JOIN ingestion_batches b"
                f" ON b.id = {batch_id} AND b.organization_id = s.organization_id"
                f" WHERE {entity_type} = 'push_batch' AND s.entity_type = 'push_batch'"
                f" AND s.entity_id = {entity_id} AND s.organization_id = {organization}"
                " AND s.event_type = 'push_batch.committed'"
            )
            continue
        owner = "p" if kind in PACKAGE_SUBJECTS else "s"
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
            if kind in PACKAGE_SUBJECTS
            else ""
        )
        queries.append(
            f"SELECT {bank_column} AS bank_id FROM {table} s{join}"
            f" WHERE {entity_type} = '{kind}' AND s.organization_id = {organization}"
            f" AND s.id = {subject_id}"
        )
        if details is not None and kind.startswith("icaap_") and kind != "icaap_cycle":
            cycle_id = uuid_sql(f"{details}->>'cycle_id'", postgresql=postgresql)
            queries.append(
                f"SELECT c.bank_id FROM icaap_cycles c WHERE {entity_type} = '{kind}'"
                f" AND c.organization_id = {organization} AND c.id = {cycle_id}"
                f" AND NOT EXISTS (SELECT 1 FROM {table} s"
                f" WHERE s.id = {identifier} AND s.organization_id = {organization})"
            )
    return "(SELECT bank_id FROM (" + " UNION ALL ".join(queries) + ") audit_subject_owner)"


def staff_bank_sql(organization: str, detail: str, *, postgresql: bool) -> str:
    """Retained ownership for historical batch and mapping staff operations."""
    batch = subject_bank_sql(
        organization,
        "'ingestion_batch'",
        f"{detail}->>'batch_id'",
        postgresql=postgresql,
        subject="ingestion_batch",
    )
    mapping = subject_bank_sql(
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


def organization_binding_sql(
    organization: str, entity_type: str, entity_id: str, *, postgresql: bool
) -> str:
    identifier = uuid_sql(entity_id, postgresql=postgresql)
    return (
        f"({entity_type} = 'authorization_binding' AND EXISTS ("
        "SELECT 1 FROM authorization_bindings s"
        f" WHERE s.organization_id = {organization} AND s.id = {identifier}"
        " AND s.institution_scope = 'organization' AND s.institution_id IS NULL))"
    )
