"""Record official credit source versions separately from calculation inputs.

Basis: BoG CRD (June 2018) ¶98, ¶123–124, in force.

Revision ID: 202610080086
Revises: 202610080085
"""

import sqlalchemy as sa

from alembic import op

revision = "202610080086"
down_revision = "202610080085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bank_reporting_periods", sa.Column("credit_source_basis", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("bank_reporting_periods", "credit_source_basis")
