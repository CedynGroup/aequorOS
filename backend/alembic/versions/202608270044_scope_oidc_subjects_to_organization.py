"""Scope OIDC subject uniqueness to the verified connection organization.

Revision ID: 202608270044
Revises: 202608250044
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608270044"
down_revision = "202608250044"
branch_labels = None
depends_on = None

_TABLE = "users"
_INDEX = "uq_users_auth_provider_sso_subject"
_PRESENT_SUBJECT = sa.text("sso_subject IS NOT NULL")


def upgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.create_index(
        _INDEX,
        _TABLE,
        ["organization_id", "auth_provider", "sso_subject"],
        unique=True,
        postgresql_where=_PRESENT_SUBJECT,
        sqlite_where=_PRESENT_SUBJECT,
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.create_index(
        _INDEX,
        _TABLE,
        ["auth_provider", "sso_subject"],
        unique=True,
        postgresql_where=_PRESENT_SUBJECT,
        sqlite_where=_PRESENT_SUBJECT,
    )
