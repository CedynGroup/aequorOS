"""Merge bank audit access and measured lineage index migration heads.

Revision ID: 202610230001
Revises: 202610120100, 202610140100

Both independent migrations follow the API/BI partition revision. Preserve
their histories and give upgrades one head without changing either schema.
"""

revision = "202610230001"
down_revision = ("202610120100", "202610140100")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
