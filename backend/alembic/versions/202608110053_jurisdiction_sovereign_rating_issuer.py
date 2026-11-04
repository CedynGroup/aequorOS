"""jurisdiction sovereign rating issuer

Revision ID: 202608110053
Revises: 202608110052
Create Date: 2026-08-11 04:00:00.000000

Canonical sovereign-rating issuer identity belongs to the global jurisdiction
registry, never to a rating calculation's country-code switch statement.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608110053"
down_revision = "202608110052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jurisdictions") as batch_op:
        batch_op.add_column(sa.Column("sovereign_rating_issuer", sa.String(length=120)))
    # Existing Ghana registry data already publishes this canonical issuer via
    # the market-data adapters; further jurisdictions are registry data, not code.
    op.execute(
        sa.text(
            "UPDATE jurisdictions SET sovereign_rating_issuer = 'GHANA_SOVEREIGN' "
            "WHERE code = 'GH'"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("jurisdictions") as batch_op:
        batch_op.drop_column("sovereign_rating_issuer")