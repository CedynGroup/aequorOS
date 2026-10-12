"""Permit API callers to ensure only nearby query-log month partitions.

Revision ID: 202610120099
Revises: 202610120098
"""

from typing import cast

import sqlalchemy as sa

from alembic import op

revision = "202610120099"
down_revision = "202610120098"
branch_labels = None
depends_on = None

_SIGNATURE = "bi_ensure_month_partition(regclass,date)"


def _api_guard(schema: str) -> str:
    parent = ('"' + schema.replace('"', '""') + '"."bi_query_log"').replace("'", "''")
    return f"""            -- API authority follows the authenticated login, even after SET ROLE.
            -- Owners retain their existing maintenance authority.
            IF pg_catalog.pg_has_role(session_user, 'aqs_api', 'USAGE')
               AND NOT pg_catalog.pg_has_role(session_user, 'aqs_owner', 'USAGE') THEN
                IF parent IS DISTINCT FROM '{parent}'::pg_catalog.regclass THEN
                    RAISE EXCEPTION 'API partition creation is limited to the BI query log'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
                IF month_start IS NULL
                   OR lower_bound < (date_trunc('month', CURRENT_DATE) - interval '1 month')::date
                   OR lower_bound > (date_trunc('month', CURRENT_DATE) + interval '1 month')::date
                THEN
                    RAISE EXCEPTION 'API query-log month must be within one month of now'
                        USING ERRCODE = 'invalid_parameter_value';
                END IF;
            END IF;
"""


def _definition() -> tuple[str, str]:
    connection = op.get_bind()
    schema = cast(str, connection.scalar(sa.text("SELECT current_schema()")))
    definition = cast(
        str,
        connection.scalar(
            sa.text("SELECT pg_get_functiondef(to_regprocedure(:signature))"),
            {"signature": _SIGNATURE},
        ),
    )
    return schema, definition


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    schema, definition = _definition()
    if (
        "SECURITY DEFINER" not in definition
        or "search_path TO 'pg_catalog', 'pg_temp'" not in definition
    ):
        raise RuntimeError("BI partition function must be a definer with a pinned search_path")
    # Keep the established child RLS/append-only policy construction intact.
    # Runtime logins receive only the new guarded entry point, never table rights.
    op.execute(definition.replace("BEGIN\n", "BEGIN\n" + _api_guard(schema), 1))
    op.execute(f"GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO aqs_api")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_SIGNATURE} FROM aqs_api")
    schema, definition = _definition()
    op.execute(definition.replace(_api_guard(schema), "", 1))
