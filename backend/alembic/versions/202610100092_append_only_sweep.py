"""Close evidence deletion gaps and seal approved assumptions, limits and grants.

Revision ID: 202610100092
Revises: 202610100091
"""

from alembic import op

revision = "202610100092"
down_revision = "202610100091"
branch_labels = None
depends_on = None

HISTORY_TABLES = (
    "scenario_assumption_history",
    "financial_manual_edit_history",
)
DELETE_GAPS = (
    "attestation_signatures",
    "regulatory_artifact_versions",
    "regulatory_package_approvals",
    "regulatory_submission_events",
    "package_stage_decisions",
    "package_workflow_stages",
    "regulatory_package_attachments",
    "regulatory_package_attachment_withdrawals",
)
PARAMETER_TABLES = (
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
SEALED_TABLES = (*PARAMETER_TABLES, "forecast_assumption_versions", "authorization_bindings")
GOVERNANCE_TABLES = (
    "regulatory_parameter",
    "system_of_record_declarations",
    "reconciliation_exceptions",
    "canonical_withdrawals",
)


def _revoke(table: str, privileges: str) -> None:
    op.execute(f"""
        REVOKE {privileges} ON {table} FROM PUBLIC;
        DO $$ DECLARE r record; BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                     WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper LOOP
                EXECUTE format('REVOKE {privileges} ON {table} FROM %I', r.rolname);
            END LOOP;
        END $$;
    """)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in HISTORY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION aequoros_append_only_guard()"
        )
        _revoke(table, "UPDATE, DELETE, TRUNCATE")
    for table in DELETE_GAPS:
        op.execute(
            f"CREATE TRIGGER {table}_retained BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION aequoros_append_only_guard()"
        )
        _revoke(table, "DELETE, TRUNCATE")
    for table in PARAMETER_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_sealed BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION aequoros_governed_row_guard("
            "'effective_to,updated_at', '', '', '', '')"
        )
    op.execute("""
        CREATE TRIGGER forecast_assumption_versions_sealed BEFORE UPDATE
          ON forecast_assumption_versions FOR EACH ROW
          EXECUTE FUNCTION aequoros_governed_row_guard(
            '', '', 'status', 'approved,rejected', '');
        CREATE TRIGGER authorization_bindings_sealed BEFORE UPDATE
          ON authorization_bindings FOR EACH ROW
          EXECUTE FUNCTION aequoros_governed_row_guard(
            'status,revoked_at,revoked_by_type,revoked_by_id,revoked_reason,updated_at',
            'revoked_at,revoked_by_type,revoked_by_id,revoked_reason', '', '', '');
        CREATE FUNCTION aequoros_binding_revocation_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF OLD.status = 'revoked' AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'binding revocation is write-once';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER authorization_bindings_revocation_lock BEFORE UPDATE
          ON authorization_bindings FOR EACH ROW
          EXECUTE FUNCTION aequoros_binding_revocation_guard();
        CREATE FUNCTION aequoros_retained_register_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF TG_TABLE_NAME = 'forecast_assumption_versions'
               AND to_jsonb(OLD)->>'status' NOT IN ('approved', 'rejected') THEN RETURN OLD; END IF;
            IF TG_TABLE_NAME IN ('regulatory_parameter', 'system_of_record_declarations')
               AND to_jsonb(OLD)->>'status' <> 'approved' THEN RETURN OLD; END IF;
            IF TG_TABLE_NAME = 'canonical_withdrawals'
               AND to_jsonb(OLD)->>'status' NOT IN ('applied', 'reversed') THEN RETURN OLD; END IF;
            RAISE EXCEPTION '% is retained evidence; DELETE is prohibited', TG_TABLE_NAME;
        END $$;
    """)
    for table in (*SEALED_TABLES, *GOVERNANCE_TABLES):
        op.execute(
            f"CREATE TRIGGER {table}_retained BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION aequoros_retained_register_guard()"
        )
        _revoke(table, "TRUNCATE")
        if table in (*PARAMETER_TABLES, "authorization_bindings", "reconciliation_exceptions"):
            _revoke(table, "DELETE")
    # Statement triggers also cover owners who retain implicit TRUNCATE rights.
    for table in (
        *HISTORY_TABLES,
        *DELETE_GAPS,
        *SEALED_TABLES,
        *GOVERNANCE_TABLES,
        "audit_events",
        "operator_audit_log",
        "audit_chain_entries",
        "audit_chain_heads",
        "regulatory_packages",
        "regulatory_runs",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION aequoros_append_only_guard()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in (
        *HISTORY_TABLES,
        *DELETE_GAPS,
        *SEALED_TABLES,
        *GOVERNANCE_TABLES,
        "audit_events",
        "operator_audit_log",
        "audit_chain_entries",
        "audit_chain_heads",
        "regulatory_packages",
        "regulatory_runs",
    ):
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
    for table in (*DELETE_GAPS, *SEALED_TABLES, *GOVERNANCE_TABLES):
        op.execute(f"DROP TRIGGER {table}_retained ON {table}")
    for table in HISTORY_TABLES:
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
    for table in SEALED_TABLES:
        op.execute(f"DROP TRIGGER {table}_sealed ON {table}")
    op.execute("DROP FUNCTION aequoros_retained_register_guard()")
    op.execute("DROP TRIGGER authorization_bindings_revocation_lock ON authorization_bindings")
    op.execute("DROP FUNCTION aequoros_binding_revocation_guard()")
    # The owner may perform earlier downgrade data transformations. App-role
    # grants remain restricted and must be provisioned for the target revision.
    for table in (*HISTORY_TABLES, *DELETE_GAPS, *SEALED_TABLES, *GOVERNANCE_TABLES):
        op.execute(f"GRANT UPDATE, DELETE, TRUNCATE ON {table} TO CURRENT_USER")
