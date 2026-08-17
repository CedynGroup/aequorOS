"""users.access_rejected_at — SSO access requests are rejected, never deleted

Revision ID: 202608160016
Revises: 202608160015

``reject_sso_access_request`` used to DELETE the never-activated JIT stub. On
the primary that 500s: ``signer_identities`` references ``users`` and the
append-only privilege tiering (202607250027) makes the FK lock fail — and
physically deleting users was never the right shape for an audited platform.
Rejection is now a recorded state on the kept (deactivated) row; a later
sign-in by the same email clears it and re-opens the request.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608160016"
down_revision = "202608160015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("access_rejected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "access_rejected_at")
