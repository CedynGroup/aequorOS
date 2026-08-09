"""Examiner role (product.md §Phase 2 item 7)

Revision ID: 202608070039
Revises: 202608070038

Widens ``ck_users_role`` to admit ``'examiner'`` — the supervisory read-only
role: it sits between analyst and viewer in the privilege ladder so it
clears every viewer-gated read (including the examiner surfaces) while every
mutation gate (analyst and above) excludes it.
"""

from __future__ import annotations

from alembic import op

revision = "202608070039"
down_revision = "202608070038"
branch_labels = None
depends_on = None

_ORIGINAL = "role IN ('admin', 'approver', 'analyst', 'viewer')"
_WIDENED = "role IN ('admin', 'approver', 'analyst', 'examiner', 'viewer')"


def upgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", _WIDENED)


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", _ORIGINAL)
