"""Examiner role (product.md §Phase 2 item 7)

Revision ID: 202608070039
Revises: 202608070038

Widens ``ck_users_role`` to admit ``'examiner'`` — the legacy supervisory
read-only role between analyst and viewer in the operational privilege ladder.
Current examiner evidence authority is owned by
``backend/docs/examiner_evidence_enforcement_rollout.md``.
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
