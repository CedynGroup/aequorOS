DO $prepare$
DECLARE
    administrator name := current_user;
    target_schema name := current_schema();
    item record;
    principal record;
    object_owner name;
    qualified text;
BEGIN
    FOR principal IN
        SELECT required.role_name, r.* FROM unnest(ARRAY[
            'aequoros_audit_owner', 'aqs_storage_recovery_owner', 'aqs_storage_recovery'
        ]) required(role_name)
        LEFT JOIN pg_roles r ON r.rolname = required.role_name
    LOOP
        IF principal.oid IS NULL OR principal.rolcanlogin OR principal.rolsuper
           OR principal.rolbypassrls OR principal.rolcreatedb
           OR principal.rolcreaterole OR principal.rolreplication THEN
            RAISE EXCEPTION 'Prepare unprivileged NOLOGIN role % before recovery',
                principal.role_name;
        END IF;
    END LOOP;
    EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO aequoros_audit_owner, '
                   'aqs_storage_recovery_owner', target_schema);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO aqs_storage_recovery', target_schema);
    FOR item IN
        SELECT * FROM (VALUES
            ('TABLE', 'aqs_storage_recovery_permit', '', 'aequoros_audit_owner',
             'SELECT, INSERT, DELETE', 'aqs_storage_recovery_owner', true),
            ('FUNCTION', 'aqs_storage_recovery_change', 'name, text, jsonb, jsonb',
             'aequoros_audit_owner', 'EXECUTE', 'PUBLIC', true),
            ('FUNCTION', 'aqs_reconcile_storage_versions', 'jsonb, text, text, text, text',
             'aqs_storage_recovery_owner', 'EXECUTE', 'aqs_storage_recovery', true),
            ('TABLE', 'banks', '', 'aqs_owner', 'SELECT', 'aqs_storage_recovery_owner', false),
            ('TABLE', 'regulatory_packages', '', 'aqs_owner',
             'SELECT', 'aqs_storage_recovery_owner', false),
            ('TABLE', 'regulatory_artifact_versions', '', 'aqs_owner',
             'SELECT, UPDATE', 'aqs_storage_recovery_owner', false),
            ('TABLE', 'regulatory_package_attachments', '', 'aqs_owner',
             'SELECT, UPDATE', 'aqs_storage_recovery_owner', false),
            ('TABLE', 'icaap_attachments', '', 'aqs_owner',
             'SELECT, UPDATE', 'aqs_storage_recovery_owner', false),
            ('TABLE', 'bank_supervisory_addons', '', 'aqs_owner',
             'SELECT, UPDATE', 'aqs_storage_recovery_owner', false),
            ('TABLE', 'audit_events', '', 'aequoros_audit_owner',
             'INSERT', 'aqs_storage_recovery_owner', false)
        ) objects(kind, object_name, arguments, intended_owner, privileges, grantee, recovery_owned)
    LOOP
        qualified := format('%I.%I', target_schema, item.object_name);
        IF item.kind = 'FUNCTION' THEN
            qualified := qualified || '(' || item.arguments || ')';
            SELECT pg_get_userbyid(proowner) INTO object_owner FROM pg_proc
                WHERE oid = to_regprocedure(qualified) AND prokind = 'f';
        ELSE
            SELECT pg_get_userbyid(relowner) INTO object_owner FROM pg_class
                WHERE oid = to_regclass(qualified) AND relkind = 'r';
        END IF;
        IF object_owner IS NULL THEN
            RAISE EXCEPTION 'Missing recovery object %; migration 202611010104 is required',
                qualified;
        END IF;
        IF object_owner <> administrator AND object_owner <> item.intended_owner THEN
            RAISE EXCEPTION 'Unexpected owner % on recovery object %; preparation refused',
                object_owner, qualified;
        END IF;
        EXECUTE format('SET LOCAL ROLE %I', object_owner);
        IF item.recovery_owned THEN
            EXECUTE format('REVOKE ALL ON %s %s FROM PUBLIC', item.kind, qualified);
            FOR principal IN SELECT rolname FROM pg_roles
                             WHERE NOT rolsuper AND rolname NOT LIKE 'pg\_%'
                               AND rolname <> object_owner
            LOOP
                EXECUTE format('REVOKE ALL ON %s %s FROM %I',
                    item.kind, qualified, principal.rolname);
            END LOOP;
        END IF;
        EXECUTE format('GRANT %s ON %s %s TO %s', item.privileges, item.kind, qualified,
            CASE WHEN item.grantee = 'PUBLIC' THEN 'PUBLIC'
                 ELSE quote_ident(item.grantee) END);
        IF item.recovery_owned AND object_owner <> item.intended_owner THEN
            EXECUTE format('ALTER %s %s OWNER TO %I',
                item.kind, qualified, item.intended_owner);
        END IF;
        EXECUTE format('SET LOCAL ROLE %I', administrator);
    END LOOP;
    EXECUTE format('REVOKE CREATE ON SCHEMA %I FROM aequoros_audit_owner, '
                   'aqs_storage_recovery_owner', target_schema);
END $prepare$;
