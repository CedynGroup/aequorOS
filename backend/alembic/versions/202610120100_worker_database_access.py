"""Expose narrow platform queue operations to the tenant-scoped worker.

Revision ID: 202610120100
Revises: 202610140100
"""

import hashlib
from typing import cast

from sqlalchemy import Connection, text

from alembic import op

revision = "202610120100"
down_revision = "202610140100"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "aqs_worker_claim_job(timestamptz,text[],text)",
    "aqs_worker_reclaim_jobs(timestamptz,jsonb)",
    "aqs_worker_seed_ticks(timestamptz)",
    "aqs_worker_enqueue_platform_daily(text,date,jsonb)",
    "aqs_worker_overdue_jobs(double precision,text[],boolean)",
    "aqs_worker_bank_key(text)",
    "aqs_worker_storage_key_required(text)",
    "aqs_worker_desk_schema()",
    "aqs_worker_audit_check(timestamptz)",
    "aqs_worker_drop_daily_partition(regclass,date)",
    "aqs_worker_ensure_month_partition(regclass,date)",
    "aqs_worker_ensure_year_partition(regclass,date)",
)


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    quote = connection.dialect.identifier_preparer.quote_identifier
    schema = quote(cast(str, connection.exec_driver_sql("SELECT current_schema()").scalar_one()))
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {schema}.jobs TO aqs_owner")
    op.execute(f"GRANT SELECT ON {schema}.banks TO aqs_owner")
    op.execute(
        f"CREATE POLICY aqs_owner_worker_queue ON {schema}.jobs "
        "TO aqs_owner USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY aqs_owner_worker_banks ON {schema}.banks "
        "FOR SELECT TO aqs_owner USING (true)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {schema}.worker_heartbeats TO aqs_worker")
    op.execute(f"""
        CREATE POLICY aqs_worker_job_bank_scope ON {schema}.jobs AS RESTRICTIVE
        FOR ALL TO aqs_worker USING (true) WITH CHECK (bank_id IS NULL OR EXISTS (
            SELECT 1 FROM {schema}.banks b WHERE b.id = jobs.bank_id
            AND b.organization_id = jobs.organization_id))
    """)
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_worker_claim_job(
            p_now timestamptz, p_types text[], p_worker text
        ) RETURNS SETOF {schema}.jobs LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
            WITH candidate AS (
                SELECT id FROM {schema}.jobs
                WHERE status = 'queued' AND (run_after IS NULL OR run_after <= p_now)
                  AND job_type = ANY(p_types)
                ORDER BY queued_at, id LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            UPDATE {schema}.jobs j SET status = 'running', started_at = p_now,
                claimed_by = p_worker FROM candidate c WHERE j.id = c.id RETURNING j.*
        $fn$
    """)
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_worker_reclaim_jobs(p_now timestamptz, p_windows jsonb)
        RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
        DECLARE j {schema}.jobs; window_seconds double precision; reclaimed integer := 0;
        BEGIN
            IF jsonb_typeof(p_windows) IS DISTINCT FROM 'object' OR EXISTS (
                SELECT 1 FROM jsonb_each_text(p_windows) WHERE value::double precision <= 0
            ) THEN RAISE EXCEPTION 'Positive per-job reclaim windows are required'; END IF;
            FOR j IN SELECT * FROM {schema}.jobs WHERE status = 'running'
                AND completed_at IS NULL AND started_at IS NOT NULL
                AND p_windows ? job_type
                AND started_at < p_now -
                    make_interval(secs => (p_windows ->> job_type)::double precision)
                FOR UPDATE SKIP LOCKED
            LOOP
                window_seconds := (p_windows ->> j.job_type)::double precision;
                UPDATE {schema}.jobs SET
                    error = 'reclaimed: running since ' || j.started_at::text ||
                        ' exceeded ' || make_interval(secs => window_seconds)::text ||
                        ' without completing (worker presumed dead)',
                    attempts = CASE WHEN j.attempts < j.max_attempts THEN j.attempts + 1
                               ELSE j.attempts END,
                    status = CASE WHEN j.attempts < j.max_attempts THEN 'queued' ELSE 'failed' END,
                    started_at = CASE WHEN j.attempts < j.max_attempts THEN NULL
                                 ELSE j.started_at END,
                    run_after = CASE WHEN j.attempts < j.max_attempts THEN p_now
                                ELSE j.run_after END,
                    completed_at = CASE WHEN j.attempts < j.max_attempts THEN NULL ELSE p_now END
                WHERE id = j.id;
                reclaimed := reclaimed + 1;
            END LOOP;
            RETURN reclaimed;
        END $fn$
    """)
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_worker_seed_ticks(p_now timestamptz)
        RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
        DECLARE org text; existing uuid; seeded integer := 0;
        BEGIN
            -- Serialize seeding independently of the claim locks; startup bursts
            -- must not create duplicate organization scheduler chains.
            PERFORM pg_advisory_xact_lock(520098);
            FOR org IN SELECT DISTINCT organization_id FROM {schema}.banks LOOP
                SELECT id INTO existing FROM {schema}.jobs
                WHERE organization_id = org AND coalesce_key = 'tick:' || org
                    AND status = 'queued' AND started_at IS NULL
                ORDER BY queued_at LIMIT 1 FOR UPDATE;
                IF existing IS NULL THEN
                    INSERT INTO {schema}.jobs(id, organization_id, job_type, status,
                        payload, progress, queued_at, run_after, coalesce_key)
                    VALUES (gen_random_uuid(), org, 'scheduled_tick', 'queued',
                        '{{}}', '{{}}', p_now, p_now, 'tick:' || org);
                ELSE
                    UPDATE {schema}.jobs SET run_after = greatest(run_after, p_now)
                    WHERE id = existing;
                END IF;
                seeded := seeded + 1;
            END LOOP;
            RETURN seeded;
        END $fn$
    """)
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_worker_overdue_jobs(
            p_stale_seconds double precision, p_types text[], p_exclude boolean
        ) RETURNS bigint LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
            SELECT count(*) FROM {schema}.jobs WHERE status = 'queued'
                AND run_after < now() - make_interval(secs => p_stale_seconds)
                AND (job_type = ANY(p_types)) <> p_exclude
        $fn$
    """)
    _platform_enqueue(schema)
    _key_access(schema)
    _desk_access(schema)
    _audit_access(schema)
    _partition_access(schema)
    legacy_roles = cast(
        list[str],
        connection.execute(
            text(
                "SELECT rolname FROM pg_roles WHERE rolbypassrls AND NOT rolsuper "
                "AND has_schema_privilege(oid, current_schema(), 'USAGE') "
                "AND has_table_privilege(oid, CAST(:jobs AS regclass), 'SELECT') "
                "AND has_table_privilege(oid, CAST(:jobs AS regclass), 'UPDATE')"
            ),
            {"jobs": f"{schema}.jobs"},
        )
        .scalars()
        .all(),
    )
    for signature in FUNCTIONS:
        function = f"{schema}.{signature}"
        op.execute(f"ALTER FUNCTION {function} OWNER TO aqs_owner")
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO aqs_worker")
        if signature != "aqs_worker_enqueue_platform_daily(text,date,jsonb)":
            for role in legacy_roles:
                op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {quote(role)}")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    quote = connection.dialect.identifier_preparer.quote_identifier
    schema = quote(cast(str, connection.exec_driver_sql("SELECT current_schema()").scalar_one()))
    view_schema = quote("aqs_worker_" + hashlib.sha256(schema.encode()).hexdigest()[:16])
    op.execute(f"DROP SCHEMA {view_schema} CASCADE")
    for table in ("desk_source_captures", "desk_observations", "desk_determinations"):
        op.execute(f"DROP FUNCTION {schema}.aqs_worker_write_{table}()")
        if not _owned_by_deployment_owner(connection, schema, table):
            op.execute(f"REVOKE INSERT, UPDATE ON {schema}.{table} FROM aqs_owner")
    for table in (
        "desk_source_captures",
        "desk_observations",
        "desk_methodologies",
        "desk_determinations",
        "desk_curve_definitions",
        "bank_encryption_keys",
    ):
        if not _owned_by_deployment_owner(connection, schema, table):
            op.execute(f"REVOKE SELECT ON {schema}.{table} FROM aqs_owner")
    op.execute(f"REVOKE UPDATE(id) ON {schema}.bank_encryption_keys FROM aqs_owner")
    for function in (
        "bi_ensure_month_partition(regclass,date)",
        "bi_ensure_year_partition(regclass,date)",
        "bi_drop_month_partition(regclass,date)",
    ):
        if not _owned_by_deployment_owner(connection, schema, function, function=True):
            op.execute(f"REVOKE EXECUTE ON FUNCTION {schema}.{function} FROM aqs_owner")
    for signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION {schema}.{signature}")
    migration_role = quote(
        cast(str, connection.exec_driver_sql("SELECT current_user").scalar_one())
    )
    op.execute("SET LOCAL ROLE aequoros_audit_owner")
    op.execute(f"REVOKE EXECUTE ON FUNCTION {schema}.aequoros_append_only_guard() FROM aqs_owner")
    for table in (
        "audit_events",
        "operator_audit_log",
        "audit_chain_entries",
        "audit_chain_heads",
        "audit_integrity_schedule",
    ):
        op.execute(f"DROP POLICY aqs_owner_worker_audit_read ON {schema}.{table}")
        if table not in {"audit_events", "operator_audit_log"}:
            op.execute(f"REVOKE SELECT ON {schema}.{table} FROM aqs_owner")
    op.execute(f"DROP POLICY aqs_owner_worker_audit_schedule ON {schema}.audit_integrity_schedule")
    op.execute(
        f"REVOKE UPDATE (run_after, recovery_epoch) "
        f"ON {schema}.audit_integrity_schedule FROM aqs_owner"
    )
    op.execute(f"SET LOCAL ROLE {migration_role}")
    op.execute(f"DROP POLICY aqs_worker_job_bank_scope ON {schema}.jobs")
    op.execute(f"DROP POLICY aqs_owner_worker_banks ON {schema}.banks")
    op.execute(f"DROP POLICY aqs_owner_worker_queue ON {schema}.jobs")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON {schema}.worker_heartbeats FROM aqs_worker")
    if not _owned_by_deployment_owner(connection, schema, "jobs"):
        op.execute(f"REVOKE SELECT, INSERT, UPDATE ON {schema}.jobs FROM aqs_owner")
    if not _owned_by_deployment_owner(connection, schema, "banks"):
        op.execute(f"REVOKE SELECT ON {schema}.banks FROM aqs_owner")


def _owned_by_deployment_owner(
    connection: Connection, schema: str, name: str, *, function: bool = False
) -> bool:
    """Downgrade removes delegation without revoking existing owner privileges."""
    catalog, owner, regtype = (
        ("pg_proc", "proowner", "regprocedure")
        if function
        else ("pg_class", "relowner", "regclass")
    )
    return cast(
        bool,
        connection.scalar(
            text(
                f"SELECT pg_get_userbyid({owner}) = 'aqs_owner' FROM pg_catalog.{catalog} "
                f"WHERE oid = CAST(:object AS {regtype})"
            ),
            {"object": f"{schema}.{name}"},
        ),
    )


def _key_access(schema: str) -> None:
    op.execute(f"GRANT SELECT, UPDATE(id) ON {schema}.bank_encryption_keys TO aqs_owner")
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_worker_storage_key_required(p_slug text)
        RETURNS boolean LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
            SELECT k.id IS NOT NULL FROM {schema}.banks b
            LEFT JOIN {schema}.bank_encryption_keys k
                ON k.bank_id = b.id AND k.organization_id = b.organization_id
            WHERE (b.storage_slug = p_slug OR k.storage_slug = p_slug)
                AND b.organization_id = nullif(current_setting('app.organization_id', true), '')
                AND (nullif(current_setting('app.bank_id', true), '') IS NULL
                     OR b.id = current_setting('app.bank_id', true))
        $fn$
    """)
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_worker_bank_key(p_slug text)
        RETURNS SETOF {schema}.bank_encryption_keys LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
            SELECT * FROM {schema}.bank_encryption_keys
            WHERE storage_slug = p_slug
                AND organization_id = nullif(current_setting('app.organization_id', true), '')
                AND (nullif(current_setting('app.bank_id', true), '') IS NULL
                     OR bank_id = current_setting('app.bank_id', true)) FOR SHARE
        $fn$
    """)


def _desk_access(schema: str) -> None:
    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote_identifier
    view_schema_name = "aqs_worker_" + hashlib.sha256(schema.encode()).hexdigest()[:16]
    view_schema = quote(view_schema_name)
    op.execute(f"CREATE SCHEMA {view_schema} AUTHORIZATION aqs_owner")
    op.execute(f"GRANT USAGE ON SCHEMA {view_schema} TO aqs_worker")
    tables = (
        "desk_source_captures",
        "desk_observations",
        "desk_methodologies",
        "desk_determinations",
        "desk_curve_definitions",
    )
    for table in tables:
        op.execute(f"GRANT SELECT ON {schema}.{table} TO aqs_owner")
        op.execute(f"CREATE VIEW {view_schema}.{table} AS SELECT * FROM {schema}.{table}")
        op.execute(f"ALTER VIEW {view_schema}.{table} OWNER TO aqs_owner")
        op.execute(f"GRANT SELECT ON {view_schema}.{table} TO aqs_worker")
        defaults = cast(
            list[tuple[str, str]],
            connection.exec_driver_sql(
                f"SELECT a.attname, pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d "
                "JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
                f"WHERE d.adrelid = '{schema}.{table}'::regclass"
            ).all(),
        )
        for column, expression in defaults:
            op.execute(
                f"ALTER VIEW {view_schema}.{table} ALTER COLUMN {quote(column)} "
                f"SET DEFAULT {expression}"
            )
    for table in ("desk_source_captures", "desk_observations", "desk_determinations"):
        op.execute(f"GRANT INSERT, UPDATE ON {schema}.{table} TO aqs_owner")
        columns = cast(
            list[str],
            connection.exec_driver_sql(
                f"SELECT attname FROM pg_attribute WHERE attrelid = '{schema}.{table}'::regclass "
                "AND attnum > 0 AND NOT attisdropped ORDER BY attnum"
            )
            .scalars()
            .all(),
        )
        assignments = ", ".join(f"{quote(column)} = NEW.{quote(column)}" for column in columns)
        if table == "desk_source_captures":
            guard = """
                IF NEW.created_by <> 'desk-capture-job@aequoros.system' OR
                   (TG_OP = 'UPDATE' AND (OLD.created_by <> NEW.created_by OR OLD.id <> NEW.id))
                THEN RAISE EXCEPTION 'Worker capture provenance is immutable'; END IF;
            """
        elif table == "desk_observations":
            guard = f"""
                IF TG_OP = 'INSERT' AND NOT EXISTS (SELECT 1 FROM {schema}.desk_source_captures
                    WHERE id = NEW.capture_id AND created_by = 'desk-capture-job@aequoros.system')
                THEN RAISE EXCEPTION 'Worker observations require robot capture lineage'; END IF;
                IF TG_OP = 'UPDATE' AND (OLD.superseded_by IS NOT NULL
                    OR (to_jsonb(NEW) - 'superseded_by' - 'updated_at'
                        - 'quality_flags' - 'attributes') IS DISTINCT FROM
                       (to_jsonb(OLD) - 'superseded_by' - 'updated_at'
                        - 'quality_flags' - 'attributes')
                    OR (NEW.attributes::jsonb - 'cross_source_mismatch') IS DISTINCT FROM
                       (OLD.attributes::jsonb - 'cross_source_mismatch'))
                THEN RAISE EXCEPTION 'Worker observations are append-only'; END IF;
            """
        else:
            guard = f"""
                IF NEW.status <> 'draft' OR NEW.prepared_by <> 'desk-capture-job@aequoros.system'
                    OR NEW.reviewed_by IS NOT NULL OR NEW.review_note IS NOT NULL
                    OR NEW.published_at IS NOT NULL OR NEW.supersedes_id IS NOT NULL
                    OR (TG_OP = 'UPDATE' AND (OLD.status <> 'draft'
                        OR OLD.prepared_by <> NEW.prepared_by
                        OR OLD.id <> NEW.id))
                THEN RAISE EXCEPTION 'Worker determinations must remain robot drafts'; END IF;
                IF NOT EXISTS (SELECT 1 FROM {schema}.desk_methodologies
                    WHERE methodology_code = NEW.methodology_code
                        AND version = NEW.methodology_version
                        AND status = 'approved' AND effective_from <= NEW.cob_date)
                THEN RAISE EXCEPTION 'Worker drafts require an approved effective methodology';
                END IF;
            """
        op.execute(f"""
            CREATE FUNCTION {schema}.aqs_worker_write_{table}() RETURNS trigger
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, {schema}, pg_temp AS $fn$
            BEGIN
                {guard}
                IF TG_OP = 'INSERT' THEN
                    INSERT INTO {schema}.{table} SELECT NEW.*;
                ELSE
                    UPDATE {schema}.{table} SET {assignments} WHERE id = OLD.id
                        AND to_jsonb({table}) = to_jsonb(OLD);
                    IF NOT FOUND THEN RAISE EXCEPTION 'Worker view row changed concurrently';
                    END IF;
                END IF;
                RETURN NEW;
            END $fn$;
            ALTER FUNCTION {schema}.aqs_worker_write_{table}() OWNER TO aqs_owner;
            REVOKE ALL ON FUNCTION {schema}.aqs_worker_write_{table}() FROM PUBLIC;
            CREATE TRIGGER worker_write INSTEAD OF INSERT OR UPDATE ON {view_schema}.{table}
                FOR EACH ROW EXECUTE FUNCTION {schema}.aqs_worker_write_{table}();
            GRANT INSERT, UPDATE ON {view_schema}.{table} TO aqs_worker;
        """)
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_worker_desk_schema() RETURNS name
        LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
            SELECT '{view_schema_name}'::name
        $fn$
    """)
    # Existing bypass queue writers opt into the same constrained robot views.
    op.execute(f"""
        DO $fn$ DECLARE r record; BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                WHERE rolbypassrls AND NOT rolsuper
                    AND has_schema_privilege(oid, current_schema(), 'USAGE')
                    AND has_table_privilege(oid, '{schema}.jobs'::regclass, 'SELECT')
                    AND has_table_privilege(oid, '{schema}.jobs'::regclass, 'UPDATE') LOOP
                EXECUTE format('GRANT USAGE ON SCHEMA {view_schema} TO %I', r.rolname);
                EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA {view_schema} TO %I',
                    r.rolname);
                EXECUTE format('GRANT INSERT, UPDATE ON {view_schema}.desk_source_captures, '
                    || '{view_schema}.desk_observations, {view_schema}.desk_determinations TO %I',
                    r.rolname);
            END LOOP;
        END $fn$
    """)


def _audit_access(schema: str) -> None:
    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote_identifier
    migration_role = quote(
        cast(str, connection.exec_driver_sql("SELECT current_user").scalar_one())
    )
    op.execute("SET LOCAL ROLE aequoros_audit_owner")
    # Partition creation clones the query-log trigger. NOINHERIT owners on
    # PostgreSQL 15 need this explicit right while audit custody stays separate.
    op.execute(f"GRANT EXECUTE ON FUNCTION {schema}.aequoros_append_only_guard() TO aqs_owner")
    for table in (
        "audit_events",
        "operator_audit_log",
        "audit_chain_entries",
        "audit_chain_heads",
        "audit_integrity_schedule",
    ):
        op.execute(f"GRANT SELECT ON {schema}.{table} TO aqs_owner")
        op.execute(
            f"CREATE POLICY aqs_owner_worker_audit_read ON {schema}.{table} "
            "FOR SELECT TO aqs_owner USING (true)"
        )
    op.execute(
        f"GRANT UPDATE (run_after, recovery_epoch) "
        f"ON {schema}.audit_integrity_schedule TO aqs_owner"
    )
    op.execute(
        f"CREATE POLICY aqs_owner_worker_audit_schedule ON {schema}.audit_integrity_schedule "
        "FOR UPDATE TO aqs_owner USING (true) WITH CHECK (true)"
    )
    op.execute(f"SET LOCAL ROLE {migration_role}")
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_worker_audit_check(p_now timestamptz)
        RETURNS TABLE(recovery_epoch bigint, results jsonb)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, {schema}, pg_temp
        SET row_security = on SET TimeZone = 'UTC' AS $fn$
        DECLARE stream_name text; result record; due boolean;
        BEGIN
            SELECT s.recovery_epoch INTO recovery_epoch FROM {schema}.audit_integrity_schedule s;
            SELECT s.id INTO due FROM {schema}.audit_integrity_schedule s
                WHERE s.run_after <= p_now FOR UPDATE SKIP LOCKED;
            IF due IS NULL THEN RETURN NEXT; RETURN; END IF;
            results := '[]'::jsonb;
            BEGIN
                FOREACH stream_name IN ARRAY ARRAY['audit_events', 'operator_audit_log'] LOOP
                    EXECUTE format($verify$
                        WITH links AS (
                            SELECT c.*, CASE WHEN e.id IS NULL THEN NULL ELSE
                                (SELECT jsonb_object_agg(key, value) FROM jsonb_each(to_jsonb(e))
                                 WHERE key = ANY(c.payload_columns)) END AS payload,
                                lag(c.entry_hash, 1, repeat('0', 64)) OVER
                                    (ORDER BY c.sequence) AS expected_previous,
                                row_number() OVER (ORDER BY c.sequence) AS expected_sequence
                            FROM {schema}.audit_chain_entries c
                            LEFT JOIN {schema}.%I e ON e.id = c.event_id
                            WHERE c.stream = $1
                        ) SELECT count(l.sequence) AS entries,
                            coalesce(bool_and(l.payload IS NOT NULL
                                AND l.sequence = l.expected_sequence
                                AND l.previous_hash = l.expected_previous
                                AND l.entry_hash = encode(sha256(convert_to(jsonb_build_array(
                                    'aequoros-audit-v1', $1, l.sequence, l.previous_hash,
                                    l.payload)::text, 'UTF8')), 'hex'))
                                FILTER (WHERE l.sequence IS NOT NULL), true)
                            AND count(l.sequence) = h.sequence
                            AND (SELECT count(*) FROM {schema}.%I) = h.sequence
                            AND coalesce((SELECT entry_hash FROM links
                                          ORDER BY sequence DESC LIMIT 1),
                                         repeat('0', 64)) = h.entry_hash AS valid
                        FROM {schema}.audit_chain_heads h LEFT JOIN links l ON true
                        WHERE h.stream = $1 GROUP BY h.sequence, h.entry_hash
                    $verify$, stream_name, stream_name) INTO STRICT result USING stream_name;
                    results := results || jsonb_build_array(jsonb_build_object(
                        'stream', stream_name, 'entries', result.entries, 'valid', result.valid));
                END LOOP;
            EXCEPTION WHEN OTHERS THEN
                results := jsonb_build_array(jsonb_build_object(
                    'stream', 'verification_unavailable', 'entries', 0, 'valid', false));
            END;
            UPDATE {schema}.audit_integrity_schedule s
                SET run_after = date_trunc('hour', p_now) + interval '1 hour',
                    recovery_epoch = s.recovery_epoch + CASE WHEN NOT EXISTS (
                        SELECT 1 FROM jsonb_array_elements(results) r
                        WHERE NOT (r ->> 'valid')::boolean
                    ) THEN 1 ELSE 0 END
                RETURNING s.recovery_epoch INTO recovery_epoch;
            RETURN NEXT;
        END $fn$
    """)


def _partition_access(schema: str) -> None:
    for function in ("bi_ensure_month_partition", "bi_ensure_year_partition"):
        op.execute(f"GRANT EXECUTE ON FUNCTION {schema}.{function}(regclass,date) TO aqs_owner")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {schema}.bi_drop_month_partition(regclass,date) TO aqs_owner"
    )
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_worker_drop_daily_partition(p_parent regclass, p_month date)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp AS $fn$
        BEGIN
            IF p_parent IS NULL OR p_parent NOT IN ('{schema}.bi_fact_position_daily'::regclass,
                                '{schema}.bi_agg_position_daily'::regclass)
                OR p_month IS NULL OR p_month >= date_trunc('month', CURRENT_DATE)::date
            THEN RAISE EXCEPTION 'Worker retention only drops completed daily-mart months'; END IF;
            RETURN {schema}.bi_drop_month_partition(p_parent, p_month);
        END $fn$
    """)

    for cadence, parents in (
        (
            "month",
            (
                "bi_fact_position_daily",
                "bi_agg_position_daily",
                "bi_fact_loan_event",
                "bi_query_log",
            ),
        ),
        ("year", ("bi_fact_position_eom",)),
    ):
        permitted = ", ".join(f"'{schema}.{parent}'::regclass" for parent in parents)
        op.execute(f"""
            CREATE FUNCTION {schema}.aqs_worker_ensure_{cadence}_partition(
                p_parent regclass, p_date date
            ) RETURNS regclass LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, {schema}, pg_temp AS $fn$
            BEGIN
                IF p_parent IS NULL OR p_parent NOT IN ({permitted}) OR p_date IS NULL
                THEN RAISE EXCEPTION 'Worker builds only ensure their classified mart partitions';
                END IF;
                RETURN {schema}.bi_ensure_{cadence}_partition(p_parent, p_date);
            END $fn$
        """)


def _platform_enqueue(schema: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_worker_enqueue_platform_daily(
            p_kind text, p_day date, p_payload jsonb
        ) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, {schema}, pg_temp SET row_security = on AS $fn$
        DECLARE org text := nullif(current_setting('app.organization_id', true), '');
            key text; created uuid;
        BEGIN
            IF p_kind IS NULL OR p_kind NOT IN ('desk_capture', 'bi_retention')
                OR p_day IS NULL OR org IS NULL
                OR NOT EXISTS (SELECT 1 FROM {schema}.banks WHERE organization_id = org)
                OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
            THEN RAISE EXCEPTION 'A scoped platform daily job is required'; END IF;
            IF p_kind = 'desk_capture' THEN
                IF p_payload IS DISTINCT FROM jsonb_build_object('cob_date', p_day::text)
                    AND p_payload IS DISTINCT FROM
                        jsonb_build_object('cob_date', p_day::text, 'auction_pass', true)
                THEN RAISE EXCEPTION 'Invalid desk capture payload'; END IF;
                key := 'desk_capture:' || p_day::text;
            ELSE
                IF NOT (p_payload ? 'builder_version')
                    OR p_payload - 'builder_version' <> '{{}}'::jsonb
                    OR jsonb_typeof(p_payload -> 'builder_version') <> 'number'
                THEN RAISE EXCEPTION 'Invalid retention payload'; END IF;
                key := 'bi-retention:' || p_day::text;
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(key, 520099));
            IF EXISTS (SELECT 1 FROM {schema}.jobs
                WHERE job_type = p_kind AND coalesce_key = key)
            THEN RETURN NULL; END IF;
            INSERT INTO {schema}.jobs(id, organization_id, job_type, status,
                payload, progress, queued_at, coalesce_key)
            VALUES (gen_random_uuid(), org, p_kind, 'queued', p_payload, '{{}}', now(), key)
            RETURNING id INTO created;
            RETURN created;
        END $fn$
    """)
