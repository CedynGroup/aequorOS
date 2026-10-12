"""Introduce deployment ownership and dormant least-privilege service roles.

Revision ID: 202610120096
Revises: 202610110095
"""

from pathlib import Path
from typing import cast

from alembic import op

revision = "202610120096"
down_revision = "202610110095"
branch_labels = None
depends_on = None

RUNTIME_ROLES = ("aqs_api", "aqs_worker", "aqs_bi", "aqs_operator", "aqs_audit_verifier")
API_JOB_UPDATE = (
    "bank_id",
    "payload",
    "run_after",
    "status",
    "started_at",
    "completed_at",
    "progress",
    "error",
)
AUDIT_READ = ("audit_events", "operator_audit_log", "audit_chain_entries", "audit_chain_heads")
GLOBAL_READ = ("jurisdictions", "institution_types", "regulatory_parameter")
BI_READ = (
    "bi_fact_position_daily",
    "bi_fact_position_eom",
    "bi_agg_position_daily",
    "bi_fact_loan_event",
    "bi_fact_gl_monthly",
    "bi_fact_gl_branch_monthly",
    "bi_fact_engine_metric",
    "bi_fact_target",
    "bi_dim_branch",
    "bi_dim_product",
    "bi_dim_counterparty",
    "bi_dim_gl_account",
    "bi_dim_date",
)
NO_DELETE = (
    "regulatory_packages",
    "regulatory_runs",
    "attestation_signatures",
    "regulatory_artifact_versions",
    "regulatory_package_approvals",
    "regulatory_submission_events",
    "package_stage_decisions",
    "package_workflow_stages",
    "regulatory_package_attachments",
    "regulatory_package_attachment_withdrawals",
    "authorization_bindings",
    "reconciliation_exceptions",
    "scenario_assumption_history",
    "financial_manual_edit_history",
    "param_lcr_runoff_rate",
    "param_nsfr_weight",
    "param_risk_weight",
    "param_stress_shock",
    "param_capital_threshold",
    "param_concentration_limit",
    "param_credit_threshold",
    "param_liquidity_threshold",
    "param_liquidity_haircut",
    "param_ecl_assumption",
    "param_crm_haircut",
)
PACKAGE_CONTENT = (
    "organization_id",
    "bank_id",
    "return_family",
    "return_code",
    "reporting_date",
    "frequency",
    "basis",
    "version",
    "supersedes_id",
    "snapshot",
    "source_runs",
    "snapshot_sha256",
    "register_state_digest",
    "is_rehearsal",
    "generated_by",
    "generated_at",
)


def _grant_verifier(name: str, table: str, tenant: bool) -> None:
    if name in AUDIT_READ:
        op.execute(f"GRANT SELECT ON {table} TO aqs_audit_verifier")
        if tenant:
            op.execute(
                f"CREATE POLICY aqs_audit_verifier_read ON {table} FOR SELECT TO "
                "aqs_audit_verifier USING (true)"
            )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    op.execute(
        Path(__file__).parents[2].joinpath("scripts/database_roles_202610120096.sql").read_text()
    )
    schema = cast(str, connection.exec_driver_sql("SELECT current_schema()").scalar_one())
    quote = connection.dialect.identifier_preparer.quote_identifier
    qualified_schema = quote(schema)
    # Administrator/local test preparation supplies SET and inheritance. An
    # application credential cannot acquire ownership through this migration.
    if not connection.exec_driver_sql(
        "SELECT pg_has_role(current_user, 'aqs_owner', 'USAGE')"
    ).scalar_one():
        raise RuntimeError(
            "Prepare aqs_owner membership using the administrator runbook before migrating"
        )
    op.execute(f"GRANT USAGE, CREATE ON SCHEMA {qualified_schema} TO aqs_owner")
    for role in RUNTIME_ROLES:
        op.execute(f"GRANT USAGE ON SCHEMA {qualified_schema} TO {role}")
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {qualified_schema} FROM {role}")
        op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {qualified_schema} FROM {role}")
        op.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {qualified_schema} FROM {role}")
    # No future table is automatically exposed. A later migration must grant
    # its classified rights; the catalogue test then holds that contract.
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE aqs_owner "
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )
    tables = connection.exec_driver_sql("""
        SELECT c.relname, c.relrowsecurity AND c.relforcerowsecurity,
               pg_get_userbyid(c.relowner),
               COALESCE(bool_or(p.polcmd = 'w' AND NOT p.polpermissive
                        AND pg_get_expr(p.polqual, p.polrelid) = 'false'), false),
               COALESCE(bool_or(p.polcmd = 'd' AND NOT p.polpermissive
                        AND pg_get_expr(p.polqual, p.polrelid) = 'false'), false)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_policy p ON p.polrelid = c.oid
        WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p')
        GROUP BY c.oid
    """).all()
    for row in tables:
        name, tenant, owner, no_update, no_delete = cast(
            tuple[str, bool, str, bool, bool], tuple(row)
        )
        table = f"{qualified_schema}.{quote(name)}"
        _grant_verifier(name, table, tenant)
        if name == "audit_events":
            op.execute(f"GRANT SELECT, INSERT ON {table} TO aqs_api, aqs_worker")
            continue
        if not tenant or owner == "aequoros_audit_owner":
            if name in GLOBAL_READ:
                op.execute(f"GRANT SELECT ON {table} TO aqs_api, aqs_worker, aqs_bi")
            continue
        rights = ["SELECT", "INSERT"]
        if not no_update and name not in {
            "regulatory_packages",
            "scenario_assumption_history",
            "financial_manual_edit_history",
        }:
            rights.append("UPDATE")
        if not no_delete and name not in NO_DELETE:
            rights.append("DELETE")
        op.execute(f"GRANT {', '.join(rights)} ON {table} TO aqs_worker")
        api_rights = [right for right in rights if name != "jobs" or right in {"SELECT", "INSERT"}]
        op.execute(f"GRANT {', '.join(api_rights)} ON {table} TO aqs_api")
        if name == "jobs":
            allowed = ", ".join(quote(column) for column in API_JOB_UPDATE)
            op.execute(f"GRANT UPDATE ({allowed}) ON {table} TO aqs_api")
            op.execute(
                f"CREATE POLICY aqs_api_job_bank_scope ON {table} AS RESTRICTIVE "
                "FOR ALL TO aqs_api USING (true) WITH CHECK (bank_id IS NULL OR EXISTS ("
                f"SELECT 1 FROM {qualified_schema}.banks b WHERE b.id = jobs.bank_id "
                "AND b.organization_id = jobs.organization_id))"
            )
        if name in BI_READ or any(name.startswith(f"{parent}_") for parent in BI_READ):
            op.execute(f"GRANT SELECT ON {table} TO aqs_bi")
        if name == "regulatory_packages":
            columns = cast(
                list[str],
                connection.exec_driver_sql(
                    "SELECT attname FROM pg_attribute WHERE attrelid = "
                    "'regulatory_packages'::regclass AND attnum > 0 AND NOT attisdropped"
                )
                .scalars()
                .all(),
            )
            allowed = ", ".join(
                quote(column) for column in columns if column not in PACKAGE_CONTENT
            )
            op.execute(f"GRANT UPDATE ({allowed}) ON {table} TO aqs_api, aqs_worker")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # Roles and credentials are cluster-wide and may serve other databases.
    # Downgrade revokes this schema's rights; it never drops shared identities
    # or restores bypass/blanket grants. Existing deployment ownership is unchanged.
    schema = cast(str, op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    op.execute(f"DROP POLICY IF EXISTS aqs_api_job_bank_scope ON {quote(schema)}.jobs")
    for table in AUDIT_READ:
        op.execute(
            f"DROP POLICY IF EXISTS aqs_audit_verifier_read ON {quote(schema)}.{quote(table)}"
        )
    for role in RUNTIME_ROLES:
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {quote(schema)} FROM {role}")
        op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {quote(schema)} FROM {role}")
        op.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {quote(schema)} FROM {role}")
        op.execute(f"REVOKE USAGE ON SCHEMA {quote(schema)} FROM {role}")
