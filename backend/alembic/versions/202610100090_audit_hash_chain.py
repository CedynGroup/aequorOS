"""Database-generated audit chains and protected stream heads.

Revision ID: 202610100090
Revises: 202610100089
"""

from typing import cast

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610100090"
down_revision = "202610100089"
branch_labels = None
depends_on = None

STREAMS = ("audit_events", "operator_audit_log")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # Migration-wide lock makes the historical boundary and the first live
    # insert atomic. Historical entries are ordered, not retroactively trusted.
    op.execute("SELECT pg_advisory_xact_lock(4130901)")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aequoros_audit_owner') THEN
                CREATE ROLE aequoros_audit_owner NOLOGIN NOSUPERUSER NOBYPASSRLS
                    NOCREATEDB NOCREATEROLE NOREPLICATION;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aequoros_audit_owner'
                       AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb
                            OR rolcreaterole OR rolreplication)) THEN
                RAISE EXCEPTION 'aequoros_audit_owner must be an unprivileged NOLOGIN role';
            END IF;
        END $$;
    """)
    # PostgreSQL 16 added the per-grant INHERIT and SET options; on 15 they are
    # implicit for every membership grant and the extended form is a SYNTAX
    # ERROR. CI runs 17 and production runs 15.13, so the newer form passed
    # every check and failed on the only server that matters.
    server_version_info = cast(
        "tuple[int, ...] | None", op.get_bind().dialect.server_version_info
    )
    if (server_version_info or (0,))[0] >= 16:
        op.execute("GRANT aequoros_audit_owner TO CURRENT_USER WITH INHERIT TRUE, SET TRUE")
    else:
        op.execute("GRANT aequoros_audit_owner TO CURRENT_USER")
    # 202608090042 revoked UPDATE, DELETE and TRUNCATE on both streams from
    # every role INCLUDING the owner, and PostgreSQL applies that ACL check to
    # LOCK ... IN ACCESS EXCLUSIVE MODE even for an owner. Without this the
    # migration cannot take its own lock. The revoke loop below strips it again
    # before this migration commits, so the identity keeps nothing.
    op.execute("GRANT UPDATE, DELETE, TRUNCATE ON audit_events, operator_audit_log TO CURRENT_USER")
    op.execute("LOCK TABLE audit_events, operator_audit_log IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        CREATE TABLE audit_chain_heads (
            stream text PRIMARY KEY,
            sequence bigint NOT NULL DEFAULT 0,
            entry_hash text NOT NULL DEFAULT repeat('0', 64)
        );
        CREATE TABLE audit_chain_entries (
            stream text NOT NULL REFERENCES audit_chain_heads(stream),
            sequence bigint NOT NULL CHECK (sequence > 0),
            event_id uuid NOT NULL,
            previous_hash text NOT NULL,
            entry_hash text NOT NULL,
            payload_columns text[] NOT NULL,
            PRIMARY KEY (stream, sequence),
            UNIQUE (stream, event_id)
        );
        INSERT INTO audit_chain_heads(stream) VALUES ('audit_events'), ('operator_audit_log');
        CREATE TABLE audit_integrity_schedule (
            id boolean PRIMARY KEY DEFAULT true CHECK (id),
            run_after timestamptz NOT NULL,
            recovery_epoch bigint NOT NULL DEFAULT 0 CHECK (recovery_epoch >= 0)
        );
        INSERT INTO audit_integrity_schedule (id, run_after) VALUES (true, '-infinity');
    """)
    schema = cast(str, op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    quoted_schema = op.get_bind().dialect.identifier_preparer.quote_identifier(str(schema))
    op.execute(f"""
        CREATE FUNCTION aequoros_chain_append(stream_name text, event_id uuid, payload jsonb)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, {quoted_schema}, pg_temp
        SET row_security = on
        SET TimeZone = 'UTC' AS $$
        DECLARE head audit_chain_heads%ROWTYPE; next_hash text;
        BEGIN
            SELECT * INTO STRICT head FROM audit_chain_heads
              WHERE stream = stream_name FOR UPDATE;
            next_hash := encode(sha256(convert_to(
                jsonb_build_array('aequoros-audit-v1', stream_name, head.sequence + 1,
                                  head.entry_hash, payload)::text, 'UTF8')), 'hex');
            INSERT INTO audit_chain_entries VALUES
                (stream_name, head.sequence + 1, event_id, head.entry_hash, next_hash,
                 ARRAY(SELECT jsonb_object_keys(payload)));
            UPDATE audit_chain_heads SET sequence = head.sequence + 1, entry_hash = next_hash
              WHERE stream = stream_name;
        END $$;
        REVOKE ALL ON FUNCTION aequoros_chain_append(text, uuid, jsonb) FROM PUBLIC;
        CREATE FUNCTION aequoros_audit_chain_insert() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, {quoted_schema}, pg_temp
        SET row_security = on
        SET TimeZone = 'UTC' AS $$
        BEGIN
            PERFORM aequoros_chain_append(TG_TABLE_NAME, NEW.id, to_jsonb(NEW));
            RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION aequoros_audit_chain_insert() FROM PUBLIC;
    """)
    # RLS-blind data migration: audit streams cover all tenants. Refuse partial
    # backfill instead of accepting whichever organization a session can see.
    op.execute("SET LOCAL TimeZone = 'UTC'")
    with force_rls_suspended(op.get_bind(), *STREAMS):
        for stream in STREAMS:
            op.execute(f"""
            DO $$ DECLARE item record; BEGIN
                FOR item IN SELECT id, to_jsonb(e) AS payload FROM {stream} e
                            ORDER BY created_at, id LOOP
                    PERFORM aequoros_chain_append('{stream}', item.id, item.payload);
                END LOOP;
            END $$;
            CREATE TRIGGER {stream}_chain_insert AFTER INSERT ON {stream}
                FOR EACH ROW EXECUTE FUNCTION aequoros_audit_chain_insert();
            """)
    op.execute("""
        CREATE TRIGGER audit_chain_entries_append_only BEFORE UPDATE OR DELETE
          ON audit_chain_entries FOR EACH ROW EXECUTE FUNCTION aequoros_append_only_guard();
    """)
    op.execute(f"GRANT USAGE, CREATE ON SCHEMA {quoted_schema} TO aequoros_audit_owner")
    for table in (*STREAMS, "audit_chain_heads", "audit_chain_entries", "audit_integrity_schedule"):
        # ALTER ... OWNER rewrites the PREVIOUS owner's own ACL entry to the new
        # owner. Where a runtime role bootstrapped the schema and still owns the
        # streams — which is production — it silently loses the SELECT and
        # INSERT it needs, and the next audit insert fails with permission
        # denied. Recorded before the transfer and re-granted after; the revoke
        # loop below still strips UPDATE, DELETE and TRUNCATE.
        previous_owner = cast(
            str,
            op.get_bind()
            .exec_driver_sql(
                "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = %(t)s::regclass",
                {"t": table},
            )
            .scalar_one(),
        )
        op.execute(f"ALTER TABLE {table} OWNER TO aequoros_audit_owner")
        if table in STREAMS and previous_owner != "aequoros_audit_owner":
            op.execute(f'GRANT SELECT, INSERT ON {table} TO "{previous_owner}"')
        if table not in STREAMS:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            REVOKE ALL ON {table} FROM PUBLIC;
            DO $$ DECLARE r record; BEGIN
                FOR r IN SELECT rolname, rolbypassrls FROM pg_roles
                         WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
                           AND rolname <> 'aequoros_audit_owner' LOOP
                    IF '{table}' IN ('audit_events', 'operator_audit_log') THEN
                        EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM %I',
                                       r.rolname);
                    ELSE
                        EXECUTE format('REVOKE ALL ON {table} FROM %I', r.rolname);
                        IF r.rolbypassrls THEN
                            EXECUTE format('GRANT SELECT ON {table} TO %I', r.rolname);
                            IF '{table}' = 'audit_integrity_schedule' THEN
                                EXECUTE format('GRANT UPDATE ON {table} TO %I', r.rolname);
                            END IF;
                        END IF;
                    END IF;
                END LOOP;
            END $$;
        """)
        if table not in STREAMS:
            op.execute(f"""
                CREATE POLICY {table}_owner ON {table} TO aequoros_audit_owner
                    USING (true) WITH CHECK (true)
            """)
    for function in (
        "aequoros_chain_append(text, uuid, jsonb)",
        "aequoros_audit_chain_insert()",
        "aequoros_append_only_guard()",
    ):
        op.execute(f"ALTER FUNCTION {function} OWNER TO aequoros_audit_owner")
        op.execute(f"""
            REVOKE ALL ON FUNCTION {function} FROM PUBLIC;
            DO $$ DECLARE r record; BEGIN
                FOR r IN SELECT rolname FROM pg_roles
                         WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
                           AND rolname <> 'aequoros_audit_owner' LOOP
                    EXECUTE format('REVOKE ALL ON FUNCTION {function} FROM %I', r.rolname);
                END LOOP;
            END $$;
        """)
    op.execute(f"REVOKE CREATE ON SCHEMA {quoted_schema} FROM aequoros_audit_owner")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for stream in STREAMS:
        op.execute(f"DROP TRIGGER {stream}_chain_insert ON {stream}")
    op.execute("DROP FUNCTION aequoros_audit_chain_insert()")
    op.execute("DROP FUNCTION aequoros_chain_append(text, uuid, jsonb)")
    op.execute("DROP TABLE audit_chain_entries, audit_chain_heads, audit_integrity_schedule")
    for table in STREAMS:
        op.execute(f"ALTER TABLE {table} OWNER TO CURRENT_USER")
    op.execute("ALTER FUNCTION aequoros_append_only_guard() OWNER TO CURRENT_USER")
