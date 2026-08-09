"""Operator control plane: operator_audit_log + tenant_storage.

Both tables are control-plane records (docs/internal/developer.md §4):
deliberately NOT RLS-forced — they are owned and read by the operator role,
never by tenant sessions, and tenant_storage.organization_id stays indexed
(via its unique constraint) for the per-tenant lookups the console makes.

operator_audit_log is append-only on Postgres: the same trigger + privilege
revocation pattern that guards audit_events (migration 202607250027). What
AequorOS staff did to tenant state must never be rewritable. No RESTRICTIVE
policies here — those only bite on RLS-enabled tables, and this one is
plain; the trigger and the REVOKE are the enforcement.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608090042"
down_revision = "202608090041"
branch_labels = None
depends_on = None

_AUDIT_TABLE = "operator_audit_log"
_STORAGE_TABLE = "tenant_storage"
_GUARD_FUNCTION = "aequoros_append_only_guard"


def upgrade() -> None:
    op.create_table(
        _AUDIT_TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("operator_email", sa.String(length=320), nullable=False),
        sa.Column("auth_mode", sa.String(length=8), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_org", sa.String(length=16), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "auth_mode IN ('dev', 'oidc')",
            name="ck_operator_audit_log_auth_mode",
        ),
    )
    op.create_index("ix_operator_audit_log_created_at", _AUDIT_TABLE, ["created_at"])
    op.create_index("ix_operator_audit_log_target_org", _AUDIT_TABLE, ["target_org"])

    op.create_table(
        _STORAGE_TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bucket_names", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("kms_key_arn", sa.String(length=2048), nullable=True),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider IN ('minio', 'aws')",
            name="ck_tenant_storage_provider",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        # Unique => indexed: the per-tenant lookup path.
        sa.UniqueConstraint("organization_id", name="uq_tenant_storage_organization_id"),
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    # Append-only enforcement (audit_events pattern). CREATE OR REPLACE keeps
    # this idempotent against the function 202607250027 already installed.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_GUARD_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'append-only table %: % is not permitted (attestation evidence)',
                TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_AUDIT_TABLE}_append_only
        BEFORE UPDATE OR DELETE ON {_AUDIT_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_GUARD_FUNCTION}()
        """
    )
    # Belt and braces: even if a future migration drops the trigger, the role
    # lacks the privilege. TRUNCATE bypasses row triggers entirely, so revoking
    # it is never optional.
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {_AUDIT_TABLE} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                     WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
            LOOP
                EXECUTE format(
                    'REVOKE UPDATE, DELETE, TRUNCATE ON {_AUDIT_TABLE} FROM %I', r.rolname
                );
            END LOOP;
        END $$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {_AUDIT_TABLE}_append_only ON {_AUDIT_TABLE}")
    op.drop_table(_STORAGE_TABLE)
    op.drop_index("ix_operator_audit_log_target_org", table_name=_AUDIT_TABLE)
    op.drop_index("ix_operator_audit_log_created_at", table_name=_AUDIT_TABLE)
    op.drop_table(_AUDIT_TABLE)
