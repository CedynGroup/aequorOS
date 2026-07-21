"""Add non-security personal profile preferences to users.

Revision ID: 202607210017
Revises: 202607200016
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607210017"
down_revision = "202607200016"
branch_labels = None
depends_on = None

TABLE = "users"
THEME_CHECK = "ck_users_theme"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("job_title", sa.String(255), nullable=True))
    op.add_column(TABLE, sa.Column("locale", sa.String(35), nullable=True))
    op.add_column(TABLE, sa.Column("timezone", sa.String(255), nullable=True))
    op.add_column(TABLE, sa.Column("theme", sa.String(16), nullable=True))
    op.create_check_constraint(
        THEME_CHECK,
        TABLE,
        "theme IS NULL OR theme IN ('light', 'dark', 'system')",
    )


def downgrade() -> None:
    op.drop_constraint(THEME_CHECK, TABLE, type_="check")
    for column in ("theme", "timezone", "locale", "job_title"):
        op.drop_column(TABLE, column)
