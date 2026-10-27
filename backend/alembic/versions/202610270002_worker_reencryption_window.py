"""Preserve size-based claim windows for native re-encryption workers.

Revision ID: 202610270002
Revises: 202610260001
"""

from typing import cast

from alembic import op

revision = "202610270002"
down_revision = "202610260001"
branch_labels = None
depends_on = None


def _replace_reaper(*, size_based: bool) -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    quote = connection.dialect.identifier_preparer.quote_identifier
    schema = quote(cast(str, connection.exec_driver_sql("SELECT current_schema()").scalar_one()))
    size_window = (
        """
                IF j.job_type = 'bank_reencrypt'
                    AND jsonb_typeof(j.payload::jsonb -> 'reencryption_bytes') = 'number'
                    AND j.payload ->> 'reencryption_bytes' ~ '^[1-9][0-9]*$'
                THEN
                    window_seconds := window_seconds +
                        8 * (j.payload ->> 'reencryption_bytes')::double precision / 1048576;
                END IF;
                IF j.started_at >= p_now - make_interval(secs => window_seconds) THEN
                    CONTINUE;
                END IF;
    """
        if size_based
        else ""
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {schema}.aqs_worker_reclaim_jobs(p_now timestamptz, p_windows jsonb)
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
                {size_window}
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


def upgrade() -> None:
    _replace_reaper(size_based=True)


def downgrade() -> None:
    _replace_reaper(size_based=False)
