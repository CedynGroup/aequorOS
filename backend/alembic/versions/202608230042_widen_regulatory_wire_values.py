"""Widen columns for admitted regulatory wire values.

Revision ID: 202608230042
Revises: 202608230043

``enterprise_stress`` is an admitted regulatory-run module (17 characters),
and ``xlsx_working`` is an admitted package artifact kind (12 characters).
Their columns remained VARCHAR(16) and VARCHAR(8), respectively, so SQLite
accepted values that PostgreSQL correctly rejected.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608230042"
down_revision = "202608230043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "regulatory_runs",
        "module",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.alter_column(
        "regulatory_package_artifacts",
        "kind",
        existing_type=sa.String(length=8),
        type_=sa.String(length=16),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "regulatory_package_artifacts",
        "kind",
        existing_type=sa.String(length=16),
        type_=sa.String(length=8),
        existing_nullable=False,
    )
    op.alter_column(
        "regulatory_runs",
        "module",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
