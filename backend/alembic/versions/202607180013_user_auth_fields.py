"""Add credential + RBAC fields to users for real authentication.

The users table carried only email/display_name/is_active (demo header-trust). Real
auth needs: a ``role`` (RBAC), an ``auth_provider`` + ``password_hash`` (Argon2id) or
``sso_subject`` (OAuth), and brute-force throttling (``failed_login_attempts`` /
``locked_until``) plus ``last_login_at``. Existing rows default to a password-provider
``viewer`` with no hash (they cannot log in until a password/SSO identity is set).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607180013"
down_revision = "202607180012"
branch_labels = None
depends_on = None

TABLE = "users"
_ROLE_CK = "ck_users_role"
_PROVIDER_CK = "ck_users_auth_provider"
_SSO_UNIQUE = "uq_users_auth_provider_sso_subject"


def upgrade() -> None:
    op.add_column(
        TABLE, sa.Column("role", sa.String(16), nullable=False, server_default="viewer")
    )
    op.add_column(
        TABLE,
        sa.Column("auth_provider", sa.String(16), nullable=False, server_default="password"),
    )
    op.add_column(TABLE, sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column(TABLE, sa.Column("sso_subject", sa.String(255), nullable=True))
    op.add_column(
        TABLE,
        sa.Column(
            "failed_login_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(TABLE, sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.create_check_constraint(
        _ROLE_CK, TABLE, "role IN ('admin', 'approver', 'analyst', 'viewer')"
    )
    op.create_check_constraint(
        _PROVIDER_CK, TABLE, "auth_provider IN ('password', 'auth0')"
    )
    op.create_index(
        _SSO_UNIQUE,
        TABLE,
        ["auth_provider", "sso_subject"],
        unique=True,
        postgresql_where=sa.text("sso_subject IS NOT NULL"),
        sqlite_where=sa.text("sso_subject IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_SSO_UNIQUE, table_name=TABLE)
    op.drop_constraint(_PROVIDER_CK, TABLE, type_="check")
    op.drop_constraint(_ROLE_CK, TABLE, type_="check")
    for column in (
        "last_login_at",
        "locked_until",
        "failed_login_attempts",
        "sso_subject",
        "password_hash",
        "auth_provider",
        "role",
    ):
        op.drop_column(TABLE, column)
