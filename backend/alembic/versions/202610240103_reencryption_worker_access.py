"""Authorize narrow worker key maintenance and audit retention counts.

Revision ID: 202610240103
Revises: 202610270001
"""

from typing import cast

from alembic import op

revision = "202610240103"
down_revision = "202610270001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "GRANT UPDATE (current_version_id, index_version_id, updated_at) "
        "ON bank_encryption_keys TO aqs_worker"
    )
    schema = cast(str, op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    quoted = op.get_bind().dialect.identifier_preparer.quote_identifier(schema)
    op.execute(f"""
        CREATE FUNCTION aequoros_retained_operator_audit_count()
        RETURNS bigint LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = on AS $$
            SELECT count(*) FROM {quoted}.operator_audit_log
            WHERE target_org = current_setting('app.organization_id', true)
        $$;
        REVOKE ALL ON FUNCTION aequoros_retained_operator_audit_count() FROM PUBLIC;
        GRANT CREATE ON SCHEMA {quoted} TO aequoros_audit_owner;
        ALTER FUNCTION aequoros_retained_operator_audit_count() OWNER TO aequoros_audit_owner;
        REVOKE CREATE ON SCHEMA {quoted} FROM aequoros_audit_owner;
        GRANT EXECUTE ON FUNCTION aequoros_retained_operator_audit_count() TO aqs_worker;
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP FUNCTION aequoros_retained_operator_audit_count()")
    op.execute(
        "REVOKE UPDATE (current_version_id, index_version_id, updated_at) "
        "ON bank_encryption_keys FROM aqs_worker"
    )
