"""Audited break-glass emergency staff sign-in.

A SEPARATE emergency-only credential bound to an explicitly designated
``operator_users`` row (``operator_break_glass_credentials``), with its own
durable throttle counter. It never widens an account's role: a successful
sign-in takes the bound row's role. The credential is default-disabled and
absent until issued.

Also widens ``ck_operator_audit_log_auth_mode`` to admit ``'break_glass'`` and
``operator_audit_log.auth_mode`` to ``VARCHAR(16)`` so every emergency attempt
is recorded with its real auth mode.

Revision ID: 202611020108
Revises: 202611020107
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202611020108"
down_revision = "202611020107"
branch_labels = None
depends_on = None

_AUDIT_TABLE = "operator_audit_log"
_CREDENTIALS_TABLE = "operator_break_glass_credentials"
_WIDENED_AUTH_MODES = "auth_mode IN ('dev', 'oidc', 'password', 'break_glass')"


def upgrade() -> None:
    op.alter_column(
        _AUDIT_TABLE,
        "auth_mode",
        existing_type=sa.String(length=8),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.drop_constraint(f"ck_{_AUDIT_TABLE}_auth_mode", _AUDIT_TABLE, type_="check")
    op.create_check_constraint(f"ck_{_AUDIT_TABLE}_auth_mode", _AUDIT_TABLE, _WIDENED_AUTH_MODES)

    op.create_table(
        _CREDENTIALS_TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("operator_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "failed_login_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["operator_user_id"], ["operator_users.id"], ondelete="CASCADE"),
        # One emergency credential per designated staff row.
        sa.UniqueConstraint("operator_user_id", name="uq_operator_break_glass_operator_user_id"),
        sa.CheckConstraint(
            "(is_enabled = false AND code_hash IS NULL) OR "
            "(is_enabled = true AND code_hash IS NOT NULL)",
            name="ck_operator_break_glass_enabled_hash",
        ),
    )


def downgrade() -> None:
    op.drop_table(_CREDENTIALS_TABLE)
