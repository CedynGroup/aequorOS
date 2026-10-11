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
    """)
    schema = cast(str, op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    quoted_schema = op.get_bind().dialect.identifier_preparer.quote_identifier(str(schema))
    op.execute(f"""
        CREATE FUNCTION aequoros_chain_append(stream_name text, event_id uuid, payload jsonb)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, {quoted_schema}, pg_temp
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
    for table in ("audit_chain_heads", "audit_chain_entries"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # No tenant policy: only the trusted definer writes, only BYPASSRLS
        # verification roles read. The heads contain no tenant-facing API data.
        op.execute(f"""
            REVOKE ALL ON {table} FROM PUBLIC;
            DO $$ DECLARE r record; BEGIN
                FOR r IN SELECT rolname, rolbypassrls FROM pg_roles
                         WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
                           AND rolname <> current_user LOOP
                    EXECUTE format('REVOKE ALL ON {table} FROM %I', r.rolname);
                    IF r.rolbypassrls THEN
                        EXECUTE format('GRANT SELECT ON {table} TO %I', r.rolname);
                    END IF;
                END LOOP;
            END $$;
        """)
    op.execute("""
        CREATE TRIGGER audit_chain_entries_append_only BEFORE UPDATE OR DELETE
          ON audit_chain_entries FOR EACH ROW EXECUTE FUNCTION aequoros_append_only_guard();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for stream in STREAMS:
        op.execute(f"DROP TRIGGER {stream}_chain_insert ON {stream}")
    op.execute("DROP FUNCTION aequoros_audit_chain_insert()")
    op.execute("DROP FUNCTION aequoros_chain_append(text, uuid, jsonb)")
    op.execute("DROP TABLE audit_chain_entries, audit_chain_heads")
