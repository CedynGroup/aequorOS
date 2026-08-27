"""Add scoped authorization bindings and authorization-version invalidation.

Revision ID: 202608250044
Revises: 202608230042

The binding table is intentionally EMPTY after upgrade.  Existing scalar roles
remain on the legacy enforcement path during shadow rollout; in particular, no
existing ``admin`` is silently converted into new operational authority or Org
Owner.  The only immediate enforcement change is token versioning: users start
at version 1, and app tokens issued before this deployment (which have no
``authv`` claim) re-authenticate once rather than being trusted without a
current authority generation.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202608250044"
down_revision = "202608230042"
branch_labels = None
depends_on = None

_TABLE = "authorization_bindings"
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "authorization_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_check_constraint(
        "ck_users_authorization_version_positive",
        "users",
        "authorization_version > 0",
    )

    # The revoker records why every refresh family ended.  Authorization changes
    # get their own evidence instead of being blurred into admin_revoked.
    op.drop_constraint("ck_refresh_tokens_revoked_reason", "refresh_tokens", type_="check")
    op.create_check_constraint(
        "ck_refresh_tokens_revoked_reason",
        "refresh_tokens",
        "revoked_reason IS NULL OR revoked_reason IN "
        "('logout', 'password_change', 'user_deactivated', 'reuse_detected', "
        "'admin_revoked', 'authorization_changed')",
    )

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("principal_user_id", sa.Uuid(), nullable=False),
        sa.Column("principal_type", sa.String(length=16), nullable=False),
        sa.Column("role_bundle", sa.String(length=32), nullable=False),
        sa.Column("institution_scope", sa.String(length=16), nullable=False),
        sa.Column("institution_id", sa.String(length=16), nullable=True),
        sa.Column("module_scope", sa.String(length=32), nullable=False),
        sa.Column("sensitivity_scope", sa.String(length=32), nullable=False),
        sa.Column("granted_by_type", sa.String(length=16), nullable=False),
        sa.Column("granted_by_id", sa.String(length=255), nullable=False),
        sa.Column("grant_reason", sa.Text(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["principal_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="CASCADE",
            name="fk_authorization_bindings_principal_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["institution_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            ondelete="RESTRICT",
            name="fk_authorization_bindings_institution_tenant",
        ),
        sa.CheckConstraint(
            "principal_type IN ('human', 'machine')",
            name="ck_authorization_bindings_principal_type",
        ),
        sa.CheckConstraint(
            "role_bundle IN "
            "('viewer', 'auditor', 'analyst', 'approver', 'account_admin', "
            "'integration_writer')",
            name="ck_authorization_bindings_role_bundle",
        ),
        sa.CheckConstraint(
            "(principal_type = 'machine' AND role_bundle = 'integration_writer') OR "
            "(principal_type = 'human' AND role_bundle <> 'integration_writer')",
            name="ck_authorization_bindings_principal_bundle",
        ),
        sa.CheckConstraint(
            "institution_scope IN ('organization', 'institution')",
            name="ck_authorization_bindings_institution_scope",
        ),
        sa.CheckConstraint(
            "(institution_scope = 'organization' AND institution_id IS NULL) OR "
            "(institution_scope = 'institution' AND institution_id IS NOT NULL)",
            name="ck_authorization_bindings_institution_target",
        ),
        sa.CheckConstraint(
            "module_scope IN "
            "('all', 'liq', 'cap', 'irrbb', 'fx', 'ftp', 'fcst', 'beh', 'data', "
            "'reg', 'risk', 'markets', 'account', 'audit')",
            name="ck_authorization_bindings_module_scope",
        ),
        sa.CheckConstraint(
            "sensitivity_scope IN ('all', 'published', 'aggregated', 'confidential', 'restricted')",
            name="ck_authorization_bindings_sensitivity_scope",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'revoked')",
            name="ck_authorization_bindings_status",
        ),
        sa.CheckConstraint(
            "granted_by_type IN ('system', 'tenant_user', 'operator')",
            name="ck_authorization_bindings_grantor_type",
        ),
        sa.CheckConstraint(
            "length(trim(granted_by_id)) > 0",
            name="ck_authorization_bindings_grantor",
        ),
        sa.CheckConstraint(
            "length(trim(grant_reason)) > 0",
            name="ck_authorization_bindings_grant_reason",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_authorization_bindings_validity_window",
        ),
        sa.CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL AND "
            "revoked_reason IS NOT NULL AND length(trim(revoked_reason)) > 0) OR "
            "(status <> 'revoked' AND revoked_at IS NULL AND revoked_reason IS NULL)",
            name="ck_authorization_bindings_revocation_state",
        ),
    )
    op.create_index(
        "ix_authorization_bindings_principal",
        _TABLE,
        ["organization_id", "principal_user_id", "status"],
    )
    op.create_index(
        "ix_authorization_bindings_institution",
        _TABLE,
        ["organization_id", "institution_id"],
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_authorization_bindings_institution", table_name=_TABLE)
    op.drop_index("ix_authorization_bindings_principal", table_name=_TABLE)
    op.drop_table(_TABLE)

    op.drop_constraint("ck_refresh_tokens_revoked_reason", "refresh_tokens", type_="check")
    with force_rls_suspended(op.get_bind(), "refresh_tokens"):
        op.execute(
            "UPDATE refresh_tokens SET revoked_reason = 'admin_revoked' "
            "WHERE revoked_reason = 'authorization_changed'"
        )
    op.create_check_constraint(
        "ck_refresh_tokens_revoked_reason",
        "refresh_tokens",
        "revoked_reason IS NULL OR revoked_reason IN "
        "('logout', 'password_change', 'user_deactivated', 'reuse_detected', "
        "'admin_revoked')",
    )
    op.drop_constraint("ck_users_authorization_version_positive", "users", type_="check")
    op.drop_column("users", "authorization_version")
