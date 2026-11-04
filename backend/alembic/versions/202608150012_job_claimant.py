"""persist job claimant runtime

Revision ID: 202608150012
Revises: 202608140011
Create Date: 2026-08-15 00:12:00.000000

The operator Operations board must identify which worker runtime moved a job
into ``running``. The nullable column supports historical queued jobs and jobs
claimed by direct test/service calls that do not represent a worker runtime.
"""

import sqlalchemy as sa

from alembic import op

revision = "202608150012"
down_revision = "202608140011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("claimed_by", sa.String(length=160), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "claimed_by")