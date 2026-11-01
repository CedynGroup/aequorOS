"""Audited, recovery-only relocation of pinned object versions.

Revision ID: 202611010104
Revises: 202610240103
"""

from __future__ import annotations

from typing import cast

from alembic import op

revision = "202611010104"
down_revision = "202610240103"
branch_labels = None
depends_on = None

OWNER = "aqs_storage_recovery_owner"
CAPABILITY = "aqs_storage_recovery"
TARGETS = {
    "regulatory_artifact_versions": "storage_version_id",
    "regulatory_package_attachments": "storage_version_id",
    "icaap_attachments": "storage_version_id",
    "bank_supervisory_addons": "letter_storage_version_id",
}
GUARDS = ("aequoros_append_only_guard", "aequoros_governed_row_guard")
EXCEPTION = """
    IF TG_OP = 'UPDATE' AND aqs_storage_recovery_change(current_user, TG_TABLE_NAME, to_jsonb(OLD),
                                                       to_jsonb(NEW)) THEN
        RETURN NEW;
    END IF;
"""


def _extend_guards(quoted_schema: str, *, remove: bool = False) -> None:
    exception = EXCEPTION.replace(
        "aqs_storage_recovery_change", quoted_schema + ".aqs_storage_recovery_change"
    )
    for function in GUARDS:
        definition = cast(
            str,
            op.get_bind()
            .exec_driver_sql(f"SELECT pg_get_functiondef('{function}()'::regprocedure)")
            .scalar_one(),
        )
        if remove:
            if definition.count(exception) != 1:
                raise RuntimeError("Recovery guard extension is missing or ambiguous.")
            definition = definition.replace(exception, "")
        else:
            if exception in definition or "BEGIN" not in definition:
                raise RuntimeError("Recovery guard cannot be installed on this definition.")
            # Keep the complete existing guard, including all lifecycle rules.
            definition = definition.replace("BEGIN", "BEGIN" + exception, 1)
        op.execute(definition)


def upgrade() -> None:  # noqa: PLR0915 - one atomic privilege and function installation
    if op.get_bind().dialect.name != "postgresql":
        return
    schema = cast(str, op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    quoted = op.get_bind().dialect.identifier_preparer.quote_identifier(schema)
    for role in (OWNER, CAPABILITY):
        op.execute(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    RAISE EXCEPTION 'Prepare {role} using the administrator runbook';
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}'
                           AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb
                                OR rolcreaterole OR rolreplication)) THEN
                    RAISE EXCEPTION '{role} must be an unprivileged NOLOGIN role';
                END IF;
            END $$;
        """)
    if not op.get_bind().exec_driver_sql(
        f"SELECT pg_has_role(current_user, '{OWNER}', 'MEMBER')"
    ).scalar_one():
        raise RuntimeError("Prepare recovery owner membership using the administrator runbook")
    op.execute(f"GRANT USAGE, CREATE ON SCHEMA {quoted} TO {OWNER}, aequoros_audit_owner")
    op.execute(f"GRANT USAGE ON SCHEMA {quoted} TO {CAPABILITY}")
    op.execute(f"""
        CREATE TABLE aqs_storage_recovery_permit (
            backend integer NOT NULL, transaction_id bigint NOT NULL,
            table_name text NOT NULL, record_id uuid NOT NULL,
            before_version text NOT NULL, after_version text NOT NULL,
            PRIMARY KEY (backend, transaction_id)
        );
        ALTER TABLE aqs_storage_recovery_permit OWNER TO aequoros_audit_owner;
        REVOKE ALL ON aqs_storage_recovery_permit FROM PUBLIC;
        DO $$ DECLARE r record; BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                     WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
                       AND rolname <> 'aequoros_audit_owner' LOOP
                EXECUTE format('REVOKE ALL ON aqs_storage_recovery_permit FROM %I', r.rolname);
            END LOOP;
        END $$;
        GRANT SELECT, INSERT, DELETE ON aqs_storage_recovery_permit TO {OWNER};
        CREATE FUNCTION aqs_storage_recovery_change(
            caller name, t text, old_row jsonb, new_row jsonb)
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, {quoted}, pg_temp AS $$
            SELECT caller = '{OWNER}'
               AND EXISTS (SELECT 1 FROM {quoted}.aqs_storage_recovery_permit p
                   WHERE p.backend = pg_backend_pid() AND p.transaction_id = txid_current()
                     AND p.table_name = t AND p.record_id = (old_row->>'id')::uuid
                     AND p.before_version = old_row->>(CASE WHEN t = 'bank_supervisory_addons'
                         THEN 'letter_storage_version_id' ELSE 'storage_version_id' END)
                     AND p.after_version = new_row->>(CASE WHEN t = 'bank_supervisory_addons'
                         THEN 'letter_storage_version_id' ELSE 'storage_version_id' END))
               AND CASE t
                 WHEN 'regulatory_artifact_versions' THEN
                     old_row - 'storage_version_id' = new_row - 'storage_version_id'
                 WHEN 'regulatory_package_attachments' THEN
                     old_row - 'storage_version_id' = new_row - 'storage_version_id'
                 WHEN 'icaap_attachments' THEN
                     old_row - 'storage_version_id' = new_row - 'storage_version_id'
                 WHEN 'bank_supervisory_addons' THEN
                     old_row - 'letter_storage_version_id' = new_row - 'letter_storage_version_id'
                 ELSE false END
        $$;
        GRANT EXECUTE ON FUNCTION aqs_storage_recovery_change(name, text, jsonb, jsonb)
            TO PUBLIC;
    """)
    op.execute(
        "ALTER FUNCTION aqs_storage_recovery_change(name, text, jsonb, jsonb) "
        "OWNER TO aequoros_audit_owner"
    )
    # The guards require the definer identity AND its private one-row permit.
    # Neither SET ROLE nor a caller-set GUC can authorize an ordinary UPDATE.
    _extend_guards(quoted)
    for table in ("banks", "regulatory_packages", *TARGETS):
        op.execute(f"GRANT SELECT ON {table} TO {OWNER}")
        op.execute(
            f"CREATE POLICY {table}_storage_recovery_read ON {table} "
            f"FOR SELECT TO {OWNER} USING (true)"
        )
    for table in TARGETS:
        op.execute(f"GRANT UPDATE ON {table} TO {OWNER}")
        op.execute(
            f"CREATE POLICY {table}_storage_recovery_update ON {table} "
            f"FOR UPDATE TO {OWNER} USING (true) WITH CHECK (true)"
        )
        if table != "bank_supervisory_addons":
            op.execute(
                f"ALTER POLICY {table}_no_update ON {table} "
                f"USING (current_user = '{OWNER}') "
                f"WITH CHECK (current_user = '{OWNER}')"
            )
    op.execute(f"GRANT INSERT ON audit_events TO {OWNER}")
    op.execute(
        f"CREATE POLICY audit_events_storage_recovery_insert ON audit_events "
        f"FOR INSERT TO {OWNER} WITH CHECK (true)"
    )
    # Schema-qualified definer writes run only in the proved recovery session;
    # installing their definitions performs no tenant-data migration.
    op.execute(f"""
        CREATE FUNCTION aqs_reconcile_storage_versions(
            report jsonb, storage_env text, reason text,
            organization text DEFAULT NULL, bank text DEFAULT NULL
        ) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, {quoted}, pg_temp
        SET row_security = on AS $fn$
        DECLARE item record; mapped jsonb; new_version text; changed integer := 0;
                affected integer;
                run_id uuid := gen_random_uuid();
                mapping_digest text := encode(sha256(convert_to(report::text, 'UTF8')), 'hex');
        BEGIN
            -- session_user survives SECURITY DEFINER and SET ROLE. A dedicated
            -- incident identity needs both explicit capability membership and
            -- cross-tenant authority, before any reference is read or changed.
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = session_user
                           AND (rolsuper OR rolbypassrls))
               OR NOT EXISTS (SELECT 1 FROM pg_auth_members m
                   JOIN pg_roles r ON r.oid = m.roleid AND r.rolname = '{CAPABILITY}'
                   JOIN pg_roles u ON u.oid = m.member AND u.rolname = session_user) THEN
                RAISE EXCEPTION 'Proved cross-tenant recovery session required';
            END IF;
            IF storage_env NOT IN ('prod', 'staging', 'dev', 'mvp')
               OR storage_env IS NULL OR length(btrim(coalesce(reason, ''))) = 0
               OR length(reason) > 2000 THEN
                RAISE EXCEPTION 'Storage environment and incident reason required';
            END IF;
            IF jsonb_typeof(report->'versions') IS DISTINCT FROM 'array'
               OR jsonb_array_length(report->'versions') = 0 THEN
                RAISE EXCEPTION 'Nonempty restore version mapping required';
            END IF;
            IF EXISTS (SELECT 1 FROM jsonb_array_elements(report->'versions') v
                WHERE jsonb_typeof(v) <> 'object' OR EXISTS (
                    SELECT 1 FROM unnest(ARRAY['bucket', 'key', 'backed_up_version_id',
                                              'restored_version_id', 'sha256']) k
                    WHERE jsonb_typeof(v->k) IS DISTINCT FROM 'string'
                       OR (length(btrim(v->>k)) = 0 AND (v->>k <> ''
                           OR k NOT IN ('backed_up_version_id', 'restored_version_id'))))
                OR length(v->>'restored_version_id') > 255
                OR (v->>'sha256') !~ '^[a-f0-9]{{64}}$') THEN
                RAISE EXCEPTION 'Incomplete restore version mapping';
            END IF;
            IF EXISTS (SELECT 1 FROM jsonb_array_elements(report->'versions') v
                       GROUP BY v->>'bucket', v->>'key', v->>'backed_up_version_id'
                       HAVING count(*) > 1)
               OR EXISTS (SELECT 1 FROM jsonb_array_elements(report->'versions') v
                       GROUP BY v->>'bucket', v->>'key', v->>'restored_version_id'
                       HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'Ambiguous restore version mapping';
            END IF;
            LOCK TABLE {quoted}.regulatory_artifact_versions,
                {quoted}.regulatory_package_attachments, {quoted}.icaap_attachments,
                {quoted}.bank_supervisory_addons
                IN SHARE ROW EXCLUSIVE MODE;
            FOR item IN
                SELECT 'regulatory_artifact_versions'::text AS table_name, v.id,
                       v.organization_id, p.bank_id, b.storage_slug, 'outputs'::text AS tier,
                       v.object_path, v.storage_version_id AS old_version,
                       'storage_version_id'::text AS column_name
                  FROM {quoted}.regulatory_artifact_versions v
                  JOIN {quoted}.regulatory_packages p ON p.id = v.package_id
                                            AND p.organization_id = v.organization_id
                  JOIN {quoted}.banks b ON b.id = p.bank_id
                       AND b.organization_id = p.organization_id
                 WHERE v.storage_version_id IS NOT NULL
                   AND (organization IS NULL OR v.organization_id = organization)
                   AND (bank IS NULL OR p.bank_id = bank)
                UNION ALL
                SELECT 'regulatory_package_attachments', v.id, v.organization_id, v.bank_id,
                       b.storage_slug, v.storage_tier, v.object_path, v.storage_version_id,
                       'storage_version_id'
                  FROM {quoted}.regulatory_package_attachments v
                  JOIN {quoted}.banks b ON b.id = v.bank_id
                       AND b.organization_id = v.organization_id
                 WHERE v.storage_version_id IS NOT NULL
                   AND (organization IS NULL OR v.organization_id = organization)
                   AND (bank IS NULL OR v.bank_id = bank)
                UNION ALL
                SELECT 'icaap_attachments', v.id, v.organization_id, v.bank_id, b.storage_slug,
                       v.storage_tier, v.object_path, v.storage_version_id, 'storage_version_id'
                  FROM {quoted}.icaap_attachments v
                  JOIN {quoted}.banks b ON b.id = v.bank_id
                       AND b.organization_id = v.organization_id
                 WHERE v.storage_version_id IS NOT NULL
                   AND (organization IS NULL OR v.organization_id = organization)
                   AND (bank IS NULL OR v.bank_id = bank)
                UNION ALL
                SELECT 'bank_supervisory_addons', v.id, v.organization_id, v.bank_id,
                       b.storage_slug, v.letter_storage_tier, v.letter_object_path,
                       v.letter_storage_version_id, 'letter_storage_version_id'
                  FROM {quoted}.bank_supervisory_addons v
                  JOIN {quoted}.banks b ON b.id = v.bank_id
                       AND b.organization_id = v.organization_id
                 WHERE v.letter_storage_version_id IS NOT NULL
                   AND (organization IS NULL OR v.organization_id = organization)
                   AND (bank IS NULL OR v.bank_id = bank)
                ORDER BY table_name, id
            LOOP
                SELECT v INTO mapped FROM jsonb_array_elements(report->'versions') v
                 WHERE v->>'bucket' = 'aequoros-' || storage_env || '-' || item.storage_slug
                                       || '-' || item.tier
                   AND v->>'key' = item.object_path
                   AND v->>'backed_up_version_id' = item.old_version;
                IF mapped IS NULL THEN
                    RAISE EXCEPTION 'Missing restore mapping for %.%', item.table_name, item.id;
                END IF;
                new_version := mapped->>'restored_version_id';
                IF length(btrim(item.old_version)) = 0
                   OR length(btrim(new_version)) = 0 THEN
                    RAISE EXCEPTION 'Incomplete restore version mapping for %.%',
                        item.table_name, item.id;
                END IF;
                INSERT INTO {quoted}.aqs_storage_recovery_permit VALUES
                    (pg_backend_pid(), txid_current(), item.table_name, item.id,
                     item.old_version, new_version);
                EXECUTE format('UPDATE %I.%I SET %I = $1 WHERE id = $2 AND %I = $3',
                    '{schema.replace(chr(39), chr(39) * 2)}', item.table_name,
                    item.column_name, item.column_name)
                    USING new_version, item.id, item.old_version;
                GET DIAGNOSTICS affected = ROW_COUNT;
                IF affected <> 1 THEN
                    RAISE EXCEPTION 'Recovery reference changed concurrently';
                END IF;
                DELETE FROM {quoted}.aqs_storage_recovery_permit
                 WHERE backend = pg_backend_pid() AND transaction_id = txid_current();
                INSERT INTO {quoted}.audit_events
                    (id, organization_id, actor_user_id, event_type, entity_type, entity_id,
                     details, created_at)
                VALUES (gen_random_uuid(), item.organization_id, NULL,
                    'storage.version_reconciled', 'storage_version_reference', item.id::text,
                    jsonb_build_object(
                        'run_id', run_id, 'database_identity', session_user, 'reason', reason,
                        'mapping_sha256', mapping_digest, 'bank_id', item.bank_id,
                        'table', item.table_name, 'record_id', item.id,
                        'column', item.column_name, 'bucket', mapped->>'bucket',
                        'object_key', item.object_path, 'before', item.old_version,
                        'after', new_version, 'ciphertext_sha256', mapped->>'sha256'), now());
                changed := changed + 1;
            END LOOP;
            IF changed = 0 THEN
                RAISE EXCEPTION 'Zero pinned references matched recovery scope';
            END IF;
            RETURN changed;
        END $fn$;
        REVOKE ALL ON FUNCTION aqs_reconcile_storage_versions(jsonb, text, text, text, text)
            FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION aqs_reconcile_storage_versions(jsonb, text, text, text, text)
            TO {CAPABILITY};
        ALTER FUNCTION aqs_reconcile_storage_versions(jsonb, text, text, text, text)
            OWNER TO {OWNER};
        REVOKE CREATE ON SCHEMA {quoted} FROM {OWNER}, aequoros_audit_owner;
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP FUNCTION aqs_reconcile_storage_versions(jsonb, text, text, text, text)")
    schema = cast(str, op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    quoted = op.get_bind().dialect.identifier_preparer.quote_identifier(schema)
    _extend_guards(quoted, remove=True)
    for table in TARGETS:
        if table != "bank_supervisory_addons":
            op.execute(
                f"ALTER POLICY {table}_no_update ON {table} USING (false) WITH CHECK (false)"
            )
        op.execute(f"DROP POLICY {table}_storage_recovery_update ON {table}")
        op.execute(f"REVOKE UPDATE ON {table} FROM {OWNER}")
    for table in ("banks", "regulatory_packages", *TARGETS):
        op.execute(f"DROP POLICY {table}_storage_recovery_read ON {table}")
        op.execute(f"REVOKE SELECT ON {table} FROM {OWNER}")
    op.execute("DROP POLICY audit_events_storage_recovery_insert ON audit_events")
    op.execute(f"REVOKE INSERT ON audit_events FROM {OWNER}")
    op.execute("DROP FUNCTION aqs_storage_recovery_change(name, text, jsonb, jsonb)")
    op.execute("DROP TABLE aqs_storage_recovery_permit")
    # Cluster roles may be used by another schema; revoke this schema only.
    op.execute(f"REVOKE USAGE ON SCHEMA {quoted} FROM {OWNER}, {CAPABILITY}")
