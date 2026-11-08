"""Stamp enqueue provenance in the worker's SQL enqueue functions (#186, D9).

The worker-only platform functions (``aqs_worker_seed_ticks``,
``aqs_worker_enqueue_platform_daily``) insert jobs directly in SQL, bypassing
``job_queue.enqueue`` — their rows would carry NULL provenance forever and be
indistinguishable from legacy rows. Defined worker/system enqueues carry an
EXPLICIT non-tenant provenance instead, so the functions now stamp the same
``worker`` principal the Python fallback path does. No locator: these jobs
have no tenant data scope. Downgrade restores the pre-provenance bodies.
"""

from __future__ import annotations

from typing import cast

from alembic import op

revision = "202611070115"
down_revision = "202611070114"
branch_labels = None
depends_on = None

_TICK_COLUMNS_UNSTAMPED = """
                    INSERT INTO {schema}.jobs(id, organization_id, job_type, status,
                        payload, progress, queued_at, run_after, coalesce_key)
                    VALUES (gen_random_uuid(), org, 'scheduled_tick', 'queued',
                        '{}', '{}', p_now, p_now, 'tick:' || org)"""
_TICK_COLUMNS_STAMPED = """
                    INSERT INTO {schema}.jobs(id, organization_id, job_type, status,
                        payload, progress, queued_at, run_after, coalesce_key,
                        enqueued_by_principal, enqueued_action)
                    VALUES (gen_random_uuid(), org, 'scheduled_tick', 'queued',
                        '{}', '{}', p_now, p_now, 'tick:' || org,
                        'worker', 'scheduler.seed_tick')"""


def _seed_ticks(schema: str, *, stamped: bool) -> str:
    columns = (_TICK_COLUMNS_STAMPED if stamped else _TICK_COLUMNS_UNSTAMPED).replace(
        "{schema}", schema
    )
    return f"""
        CREATE OR REPLACE FUNCTION {schema}.aqs_worker_seed_ticks(p_now timestamptz)
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
                IF existing IS NULL THEN{columns};
                ELSE
                    UPDATE {schema}.jobs SET run_after = greatest(run_after, p_now)
                    WHERE id = existing;
                END IF;
                seeded := seeded + 1;
            END LOOP;
            RETURN seeded;
        END $fn$
    """


def _platform_enqueue(schema: str, *, stamped: bool) -> str:
    if stamped:
        insert = """
            INSERT INTO {schema}.jobs(id, organization_id, job_type, status,
                payload, progress, queued_at, coalesce_key,
                enqueued_by_principal, enqueued_action)
            VALUES (gen_random_uuid(), org, p_kind, 'queued', p_payload, '{}', now(), key,
                'worker', 'platform.daily:' || p_kind)
            RETURNING id INTO created"""
    else:
        insert = """
            INSERT INTO {schema}.jobs(id, organization_id, job_type, status,
                payload, progress, queued_at, coalesce_key)
            VALUES (gen_random_uuid(), org, p_kind, 'queued', p_payload, '{}', now(), key)
            RETURNING id INTO created"""
    insert = insert.replace("{schema}", schema)
    return f"""
        CREATE OR REPLACE FUNCTION {schema}.aqs_worker_enqueue_platform_daily(
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
            {insert};
            RETURN created;
        END $fn$
    """


def _quoted_schema() -> str:
    # Raw SQLAlchemy scalars have no static type; validate this SQL boundary.
    schema = cast(object, op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    if not isinstance(schema, str):
        raise RuntimeError("Expected a migration schema")
    return op.get_bind().dialect.identifier_preparer.quote_identifier(schema)


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    schema = _quoted_schema()
    op.execute(_seed_ticks(schema, stamped=True))
    op.execute(_platform_enqueue(schema, stamped=True))


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    schema = _quoted_schema()
    op.execute(_seed_ticks(schema, stamped=False))
    op.execute(_platform_enqueue(schema, stamped=False))
