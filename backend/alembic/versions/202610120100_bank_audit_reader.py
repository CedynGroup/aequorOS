"""Officer bundles and tenant/bank-scoped audit verification reads.

Revision ID: 202610120100
Revises: 202610120099

No bindings are backfilled: a bank must explicitly assign its officers.
"""

from typing import cast

import sqlalchemy as sa

from alembic import op
from app.core.audit_attribution import (
    SUBJECT_TABLES,
    organization_binding_sql,
    staff_bank_sql,
    subject_bank_sql,
    uuid_sql,
)

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
_OWNED_BANK = subject_bank_sql(
    "e.organization_id", "e.entity_type", "e.entity_id", postgresql=True, details="e.details"
)
_ORGANIZATION_BINDING = organization_binding_sql(
    "e.organization_id", "e.entity_type", "e.entity_id", postgresql=True
)
_TENANT_MATCH = f"""
    e.organization_id = {_ORG}
    AND (e.details->>'bank_id' = {_BANK}
         OR (e.entity_type = 'bank' AND e.entity_id = {_BANK})
         OR e.details->'scope'->>'institution_id' = {_BANK}
         OR (e.entity_type = 'authorization_access_request'
             AND e.details->>'institution_id' = {_BANK})
         OR {_OWNED_BANK} = {_BANK}
         OR {_ORGANIZATION_BINDING})
    AND (e.details->>'bank_id' IS NULL OR e.details->>'bank_id' = {_BANK})
    AND (e.details->'scope'->>'institution_id' IS NULL
         OR e.details->'scope'->>'institution_id' = {_BANK})
    AND (e.entity_type IS DISTINCT FROM 'bank' OR e.entity_id = {_BANK})
    AND ({_OWNED_BANK} IS NULL OR {_OWNED_BANK} = {_BANK})
    AND (e.entity_type IS DISTINCT FROM 'authorization_access_request'
         OR e.details->>'institution_id' IS NULL OR e.details->>'institution_id' = {_BANK})
"""
_STAFF_OWNED_BANK = staff_bank_sql("e.target_org", "e.detail", postgresql=True)
_STAFF_MATCH = f"""
    e.target_org = {_ORG}
    AND (e.detail->>'bank_id' = {_BANK} OR {_STAFF_OWNED_BANK} = {_BANK})
    AND (e.detail->>'bank_id' IS NULL OR e.detail->>'bank_id' = {_BANK})
    AND ({_STAFF_OWNED_BANK} IS NULL OR {_STAFF_OWNED_BANK} = {_BANK})
    AND (e.detail->'after'->>'bank_id' IS NULL OR e.detail->'after'->>'bank_id' = {_BANK})
"""


_OWNERSHIP_TABLES = sorted(set(SUBJECT_TABLES.values()) - {"audit_events"})


def upgrade() -> None:
    op.add_column(
        "operator_inspector_sessions", sa.Column("support_reference", sa.String(200), nullable=True)
    )
    op.create_index(
        "ix_audit_events_subject",
        "audit_events",
        ["organization_id", "entity_type", "entity_id", "event_type"],
    )
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
    op.execute(f"GRANT SELECT ON {', '.join(_OWNERSHIP_TABLES)} TO aequoros_audit_owner")
    op.execute(f"""
        ALTER TABLE operator_audit_log ENABLE ROW LEVEL SECURITY;
        ALTER TABLE operator_audit_log FORCE ROW LEVEL SECURITY;
        CREATE POLICY bank_audit_staff_owner ON operator_audit_log TO aequoros_audit_owner
            USING (true) WITH CHECK (true);
        CREATE POLICY bank_audit_staff_insert ON operator_audit_log FOR INSERT WITH CHECK (true);
        CREATE POLICY bank_audit_staff_verifier ON operator_audit_log FOR SELECT
            TO aqs_audit_verifier, aqs_operator USING (true);
        CREATE POLICY bank_audit_staff_read ON operator_audit_log FOR SELECT USING (
            {_STAFF_MATCH.replace("e.", "operator_audit_log.")});
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
    op.execute(f"""
        CREATE FUNCTION bank_audit_inspection_context(wanted_stream text, wanted_event uuid)
        RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, {quoted}, pg_temp AS $fn$
            WITH evidence AS (
                SELECT e.organization_id, e.details->>'act_operator' AS operator_email,
                       e.details->>'impersonation_session_id' AS session_id
                FROM audit_events e WHERE wanted_stream = 'audit_events'
                    AND e.id = wanted_event AND {_TENANT_MATCH}
                UNION ALL
                SELECT e.target_org, e.operator_email, e.detail->>'session_id'
                FROM operator_audit_log e WHERE wanted_stream = 'operator_audit_log'
                    AND e.id = wanted_event AND {_STAFF_MATCH}
            )
            SELECT jsonb_build_object('reason', s.reason, 'support_reference', s.support_reference)
            FROM evidence e JOIN operator_inspector_sessions s
                ON s.id = {uuid_sql("e.session_id", postgresql=True)}
                AND s.organization_id = e.organization_id AND s.started_by = e.operator_email
        $fn$;
    """)
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
    op.execute("""
        REVOKE ALL ON FUNCTION bank_audit_previous_link(text,bigint,text) FROM PUBLIC;
        REVOKE ALL ON FUNCTION bank_audit_inspection_context(text,uuid) FROM PUBLIC;
        GRANT SELECT ON operator_audit_log, audit_chain_entries TO aqs_api;
        DO $$ DECLARE r record; BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
                AND has_table_privilege(rolname, 'audit_chain_entries', 'SELECT') LOOP
                EXECUTE format('GRANT EXECUTE ON FUNCTION '
                    'bank_audit_previous_link(text,bigint,text) TO %I', r.rolname);
                EXECUTE format('GRANT EXECUTE ON FUNCTION '
                    'bank_audit_inspection_context(text,uuid) TO %I', r.rolname);
            END LOOP;
        END $$;
    """)


def downgrade() -> None:
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
        f"role_bundle IN ({_BEFORE})",
    )
