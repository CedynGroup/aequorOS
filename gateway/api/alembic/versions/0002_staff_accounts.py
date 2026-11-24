# pyright: standard
"""staff accounts for the gateway's own identity provider

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_accounts",
        sa.Column("subject", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("given_name", sa.String(length=100), nullable=True),
        sa.Column("family_name", sa.String(length=100), nullable=True),
        sa.Column("groups", sa.JSON(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sign_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.PrimaryKeyConstraint("subject", name=op.f("pk_staff_accounts")),
        sa.UniqueConstraint("email", name=op.f("uq_staff_accounts_email")),
    )
    role = _app_role()
    if role and op.get_bind().dialect.name == "postgresql":
        # The API signs staff in and records lockouts; an account is disabled, never deleted.
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON staff_accounts TO {role}")


def _app_role() -> str | None:
    """The API's database role, as 0001 reads it: ``-x app_role=`` or ``GATEWAY_DB_APP_ROLE``."""
    import os
    import re

    from alembic import context

    role = context.get_x_argument(as_dictionary=True).get("app_role") or os.environ.get(
        "GATEWAY_DB_APP_ROLE"
    )
    if role and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("app_role must be a plain lower-case identifier")
    return role or None


def downgrade() -> None:
    op.drop_table("staff_accounts")
