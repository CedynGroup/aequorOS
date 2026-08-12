"""Operator password auth: operator_users + 'password' audit auth_mode.

Staff (operator) authentication is rebuilt to MATCH the client-side model —
email+password primary, workforce SSO secondary. ``operator_users`` is the
staff account table (GLOBAL, deliberately NOT RLS-forced — the operator
control-plane precedent of migration 202608090042), holding the same
Argon2id password hashes the tenant ``users`` table uses.

Also widens ``ck_operator_audit_log_auth_mode`` to admit ``'password'``
(the 202608070039 examiner-role CHECK-swap pattern): every action taken
under an operator-JWT session is recorded with its real auth mode.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608110045"
down_revision = "202608090044"
branch_labels = None
depends_on = None

_USERS_TABLE = "operator_users"
_AUDIT_TABLE = "operator_audit_log"
_ORIGINAL_AUTH_MODES = "auth_mode IN ('dev', 'oidc')"
_WIDENED_AUTH_MODES = "auth_mode IN ('dev', 'oidc', 'password')"


def upgrade() -> None:
    op.create_table(
        _USERS_TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('developer', 'operator_admin', 'super_admin')",
            name="ck_operator_users_role",
        ),
        # Unique => indexed: the login lookup path. One identity per email.
        sa.UniqueConstraint("email", name="uq_operator_users_email"),
    )

    op.drop_constraint(f"ck_{_AUDIT_TABLE}_auth_mode", _AUDIT_TABLE, type_="check")
    op.create_check_constraint(f"ck_{_AUDIT_TABLE}_auth_mode", _AUDIT_TABLE, _WIDENED_AUTH_MODES)


def downgrade() -> None:
    op.drop_constraint(f"ck_{_AUDIT_TABLE}_auth_mode", _AUDIT_TABLE, type_="check")
    op.create_check_constraint(f"ck_{_AUDIT_TABLE}_auth_mode", _AUDIT_TABLE, _ORIGINAL_AUTH_MODES)
    op.drop_table(_USERS_TABLE)
