"""desk curve entitlement tier (FC-6d)

Revision ID: 202608120054
Revises: 202608110053
Create Date: 2026-08-12 00:00:00.000000

Curve-platform §10 tiering (FC-6d): a governed forward-curve definition carries
a distribution ``entitlement_tier`` (core / standard / premium). A published
curve's tier IS its definition's tier; market-data reads gate an org to curves
at or below its active dataset tier. ONE additive column on the GLOBAL
``desk_curve_definitions`` table plus a CHECK constraint — existing rows adopt
the ``standard`` default (the platform-wide grandfather tier).

SQLite-createable (the hermetic suite builds via ``Base.metadata.create_all``);
``downgrade`` drops the column (the CHECK rides on it).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608120054"
down_revision = "202608110053"
branch_labels = None
depends_on = None

_TABLE = "desk_curve_definitions"
_COLUMN = "entitlement_tier"
_CHECK = "ck_desk_curve_definitions_entitlement_tier"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.String(length=20),
            server_default=sa.text("'standard'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "entitlement_tier IN ('core', 'standard', 'premium')",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, _COLUMN)
