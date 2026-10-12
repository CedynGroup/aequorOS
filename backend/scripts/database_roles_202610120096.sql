-- PostgreSQL 17 role foundation. No credentials or runtime cutover here.
DO $$
DECLARE role_name text;
BEGIN
    PERFORM pg_advisory_xact_lock(520096);
    FOREACH role_name IN ARRAY ARRAY[
        'aqs_owner', 'aqs_migrator', 'aqs_api', 'aqs_worker', 'aqs_bi',
        'aqs_operator', 'aqs_audit_verifier', 'aequoros_audit_owner'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'CREATE ROLE %I %s NOSUPERUSER NOCREATEDB NOCREATEROLE '
                'NOINHERIT NOREPLICATION NOBYPASSRLS', role_name,
                CASE WHEN role_name IN ('aqs_owner', 'aequoros_audit_owner')
                     THEN 'NOLOGIN' ELSE 'LOGIN' END);
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = role_name
            AND ((rolinherit AND role_name <> 'aequoros_audit_owner') OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
                 OR rolcanlogin <> (role_name NOT IN ('aqs_owner', 'aequoros_audit_owner')))
        ) THEN
            RAISE EXCEPTION 'Unsafe attributes on database role %; administrator preparation required', role_name;
        END IF;
    END LOOP;
    IF NOT pg_has_role('aqs_migrator', 'aqs_owner', 'MEMBER') THEN
        GRANT aqs_owner TO aqs_migrator WITH INHERIT FALSE, SET TRUE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aqs_bi'
                   AND rolconfig @> ARRAY['statement_timeout=10s']) THEN
        ALTER ROLE aqs_bi SET statement_timeout = '10s';
    END IF;
    -- Historical integrity migrations retain their dedicated owner and need
    -- the deployment owner to administer that NOLOGIN role's membership.
    IF NOT pg_has_role('aqs_owner', 'aequoros_audit_owner', 'MEMBER') THEN
        GRANT aequoros_audit_owner TO aqs_owner WITH ADMIN TRUE, INHERIT TRUE, SET TRUE;
    END IF;
END $$;
