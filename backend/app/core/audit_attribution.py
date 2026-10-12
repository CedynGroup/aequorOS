"""Bank ownership of retained Data Engine and filing audit subjects."""

SUBJECT_TABLES = {
    "ingestion_batch": "ingestion_batches",
    "mapping_config": "mapping_configs",
    "canonical_position_snapshot": "canonical_position_snapshots",
    "regulatory_run": "regulatory_runs",
    "regulatory_package": "regulatory_packages",
    "regulatory_package_artifact": "regulatory_package_artifacts",
    "regulatory_artifact_version": "regulatory_artifact_versions",
}
PACKAGE_SUBJECTS = {"regulatory_package_artifact", "regulatory_artifact_version"}


def subject_bank_sql(
    organization: str,
    entity_type: str,
    entity_id: str,
    *,
    postgresql: bool,
    subject: str | None = None,
) -> str:
    identifier = (
        f"CASE WHEN {entity_id} ~ '^[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-"
        f"[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}}$'"
        f" THEN CAST({entity_id} AS uuid) END"
        if postgresql
        else f"lower(replace({entity_id}, '-', ''))"
    )
    queries: list[str] = []
    for kind, table in SUBJECT_TABLES.items():
        if subject is not None and kind != subject:
            continue
        owner = "p" if kind in PACKAGE_SUBJECTS else "s"
        join = (
            " JOIN regulatory_packages p ON p.id = s.package_id"
            " AND p.organization_id = s.organization_id"
            if kind in PACKAGE_SUBJECTS
            else ""
        )
        queries.append(
            f"SELECT {owner}.bank_id FROM {table} s{join}"
            f" WHERE {entity_type} = '{kind}' AND s.organization_id = {organization}"
            f" AND s.id = {identifier}"
        )
    return "(SELECT bank_id FROM (" + " UNION ALL ".join(queries) + ") audit_subject_owner)"
