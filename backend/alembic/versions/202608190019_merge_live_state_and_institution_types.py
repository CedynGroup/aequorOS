"""Merge live-state and institution-type migration heads.

Revision ID: 202608190019
Revises: 202608190017, 202608190018
Create Date: 2026-08-19 12:15:00.000000

The live-first Treasury migration and the institution-type registry were
created independently from the same prior head. This merge is intentionally
schema-neutral: both parent revisions must run, then future upgrades have one
unambiguous target.
"""

revision = "202608190019"
down_revision = ("202608190017", "202608190018")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
