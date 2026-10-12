"""Officer bundles and tenant/bank-scoped audit verification reads.

Revision ID: 202610120100
Revises: 202610120099

No bindings are backfilled: a bank must explicitly assign its officers.
"""

from typing import cast

from alembic import op

revision = "202610120100"
down_revision = "202610120099"
branch_labels = None
depends_on = None

_BEFORE = (
    "'member','viewer','auditor','analyst','approver','validator',"
    "'account_admin','org_owner','integration_writer','bi_reader'"
)
_BANK = "current_setting('app.audit_bank_id', true)"
_ORG = "current_setting('app.organization_id', true)"
_TENANT_MATCH = f"""
    e.organization_id = {_ORG}
    AND (e.details->>'bank_id' = {_BANK}
         OR (e.entity_type = 'bank' AND e.entity_id = {_BANK})
         OR e.details->'scope'->>'institution_id' = {_BANK})
    AND (e.details->>'bank_id' IS NULL OR e.details->>'bank_id' = {_BANK})
    AND (e.details->'scope'->>'institution_id' IS NULL
         OR e.details->'scope'->>'institution_id' = {_BANK})
    AND (e.entity_type IS DISTINCT FROM 'bank' OR e.entity_id = {_BANK})
"""
_STAFF_MATCH = f"e.target_org = {_ORG} AND e.detail->>'bank_id' = {_BANK}"


def upgrade() -> None:
    op.drop_constraint(
        "ck_authorization_bindings_role_bundle", "authorization_bindings", type_="check"
    )
    op.create_check_constraint(
        "ck_authorization_bindings_role_bundle",
        "authorization_bindings",
        f"role_bundle IN ({_BEFORE},'security_officer','compliance_officer')",
    )
    op.create_check_constraint(
        "ck_authorization_bindings_audit_officer_scope",
        "authorization_bindings",
        "role_bundle NOT IN ('security_officer', 'compliance_officer') OR "
        "(module_scope = 'audit' AND data_scope_kind = 'all')",
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    schema = cast(str, op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    quoted = op.get_bind().dialect.identifier_preparer.quote_identifier(schema)
    op.execute(f"""
        ALTER TABLE operator_audit_log ENABLE ROW LEVEL SECURITY;
        ALTER TABLE operator_audit_log FORCE ROW LEVEL SECURITY;
        CREATE POLICY bank_audit_staff_owner ON operator_audit_log TO aequoros_audit_owner
            USING (true) WITH CHECK (true);
        CREATE POLICY bank_audit_staff_insert ON operator_audit_log FOR INSERT WITH CHECK (true);
        CREATE POLICY bank_audit_staff_verifier ON operator_audit_log FOR SELECT
            TO aqs_audit_verifier, aqs_operator USING (true);
        CREATE POLICY bank_audit_staff_read ON operator_audit_log FOR SELECT USING (
            target_org = {_ORG} AND detail->>'bank_id' = {_BANK});
        CREATE POLICY bank_audit_entries_read ON audit_chain_entries FOR SELECT USING (
            (stream = 'audit_events' AND EXISTS (
                SELECT 1 FROM audit_events e WHERE e.id = audit_chain_entries.event_id
                AND {_TENANT_MATCH}))
            OR (stream = 'operator_audit_log' AND EXISTS (
                SELECT 1 FROM operator_audit_log e WHERE e.id = audit_chain_entries.event_id
                AND {_STAFF_MATCH})));
    """)
    # The definer returns a boolean for an attributable event, never another
    # tenant's source row or the preceding global entry's payload/hash.
    op.execute(f"""
        CREATE FUNCTION bank_audit_previous_link(
            wanted_stream text, wanted_sequence bigint, wanted_hash text)
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, {quoted}, pg_temp AS $fn$
            SELECT EXISTS (
                SELECT 1 FROM audit_chain_entries c WHERE c.stream = wanted_stream
                AND c.sequence = wanted_sequence AND c.previous_hash = wanted_hash
                AND ((c.stream = 'audit_events' AND EXISTS (
                    SELECT 1 FROM audit_events e WHERE e.id = c.event_id AND {_TENANT_MATCH}))
                OR (c.stream = 'operator_audit_log' AND EXISTS (
                    SELECT 1 FROM operator_audit_log e WHERE e.id = c.event_id AND {_STAFF_MATCH})))
                AND c.previous_hash = CASE WHEN c.sequence = 1 THEN repeat('0', 64)
                    ELSE (SELECT p.entry_hash FROM audit_chain_entries p
                          WHERE p.stream = c.stream AND p.sequence = c.sequence - 1) END)
        $fn$;
    """)
    # PostgreSQL requires the new function owner to have schema CREATE during
    # ownership transfer. The audit owner retains custody, never schema CREATE.
    op.execute(f"GRANT CREATE ON SCHEMA {quoted} TO aequoros_audit_owner")
    op.execute(
        "ALTER FUNCTION bank_audit_previous_link(text,bigint,text) OWNER TO aequoros_audit_owner"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA {quoted} FROM aequoros_audit_owner")
    op.execute("""
        REVOKE ALL ON FUNCTION bank_audit_previous_link(text,bigint,text) FROM PUBLIC;
        GRANT SELECT ON operator_audit_log, audit_chain_entries TO aqs_api;
        DO $$ DECLARE r record; BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
                AND has_table_privilege(rolname, 'audit_chain_entries', 'SELECT') LOOP
                EXECUTE format('GRANT EXECUTE ON FUNCTION '
                    'bank_audit_previous_link(text,bigint,text) TO %I', r.rolname);
            END LOOP;
        END $$;
    """)


def downgrade() -> None:
    op.drop_constraint(
        "ck_authorization_bindings_audit_officer_scope", "authorization_bindings", type_="check"
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION bank_audit_previous_link(text,bigint,text)")
        op.execute("DROP POLICY bank_audit_entries_read ON audit_chain_entries")
        for name in ("owner", "insert", "verifier", "read"):
            op.execute(f"DROP POLICY bank_audit_staff_{name} ON operator_audit_log")
        op.execute("ALTER TABLE operator_audit_log NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE operator_audit_log DISABLE ROW LEVEL SECURITY")
        op.execute("REVOKE SELECT ON operator_audit_log, audit_chain_entries FROM aqs_api")
    op.drop_constraint(
        "ck_authorization_bindings_role_bundle", "authorization_bindings", type_="check"
    )
    op.create_check_constraint(
        "ck_authorization_bindings_role_bundle",
        "authorization_bindings",
        f"role_bundle IN ({_BEFORE})",
    )
