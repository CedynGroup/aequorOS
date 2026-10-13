-- Role foundation for PostgreSQL 15+ (production runs 15.13; CI runs 17). No credentials or runtime cutover here.
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
    -- Per-grant INHERIT/SET options are PostgreSQL 16+; on 15 they are a SYNTAX
    -- ERROR, and production runs 15.13. EXECUTE keeps the newer form unparsed on
    -- older servers. On 15, SET ROLE is always permitted to a member and
    -- inheritance follows the MEMBER's attribute — aqs_migrator is NOINHERIT
    -- (enforced above), so the plain grant gives exactly SET-without-inherit.
    IF NOT pg_has_role('aqs_migrator', 'aqs_owner', 'MEMBER') THEN
        IF current_setting('server_version_num')::int >= 160000 THEN
            EXECUTE 'GRANT aqs_owner TO aqs_migrator WITH INHERIT FALSE, SET TRUE';
        ELSE
            EXECUTE 'GRANT aqs_owner TO aqs_migrator';
        END IF;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aqs_bi'
                   AND rolconfig @> ARRAY['statement_timeout=10s']) THEN
        ALTER ROLE aqs_bi SET statement_timeout = '10s';
    END IF;
    -- Historical integrity migrations retain their dedicated owner and need
    -- the deployment owner to administer that NOLOGIN role's membership.
    -- On 15, ADMIN OPTION is the load-bearing part: the comment above names
    -- administering that NOLOGIN role's membership as the need. INHERIT cannot
    -- be granted per-membership on 15 and aqs_owner is NOINHERIT, so on 15
    -- aqs_owner reaches aequoros_audit_owner's privileges by SET ROLE rather
    -- than automatically. Flagged for the administrator dry run to confirm.
    IF NOT pg_has_role('aqs_owner', 'aequoros_audit_owner', 'MEMBER') THEN
        IF current_setting('server_version_num')::int >= 160000 THEN
            EXECUTE 'GRANT aequoros_audit_owner TO aqs_owner WITH ADMIN TRUE, INHERIT TRUE, SET TRUE';
        ELSE
            EXECUTE 'GRANT aequoros_audit_owner TO aqs_owner WITH ADMIN OPTION';
        END IF;
    END IF;
END $$;
