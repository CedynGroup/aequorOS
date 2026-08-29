"""Record the actor behind every scoped-binding revocation.

Revision ID: 202608290047
Revises: 202608280046

Grant administration makes revocation a tenant-facing governance act.  The
existing row already recorded when and why a binding ended; this revision adds
the equally important who, and tightens the lifecycle check so incomplete
revocation evidence cannot be persisted.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608290047"
down_revision = "202608280046"
branch_labels = None
depends_on = None

_TABLE = "authorization_bindings"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("revoked_by_type", sa.String(length=16), nullable=True))
    op.add_column(_TABLE, sa.Column("revoked_by_id", sa.String(length=255), nullable=True))
    op.create_check_constraint(
        "ck_authorization_bindings_revoker_type",
        _TABLE,
        "revoked_by_type IS NULL OR revoked_by_type IN ('system', 'tenant_user', 'operator')",
    )
    op.drop_constraint("ck_authorization_bindings_revocation_state", _TABLE, type_="check")
    op.create_check_constraint(
        "ck_authorization_bindings_revocation_state",
        _TABLE,
        "(status = 'revoked' AND revoked_at IS NOT NULL AND "
        "revoked_by_type IS NOT NULL AND revoked_by_id IS NOT NULL AND "
        "length(trim(revoked_by_id)) > 0 AND revoked_reason IS NOT NULL AND "
        "length(trim(revoked_reason)) > 0) OR "
        "(status <> 'revoked' AND revoked_at IS NULL AND revoked_by_type IS NULL AND "
        "revoked_by_id IS NULL AND revoked_reason IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_authorization_bindings_revocation_state", _TABLE, type_="check")
    op.create_check_constraint(
        "ck_authorization_bindings_revocation_state",
        _TABLE,
        "(status = 'revoked' AND revoked_at IS NOT NULL AND "
        "revoked_reason IS NOT NULL AND length(trim(revoked_reason)) > 0) OR "
        "(status <> 'revoked' AND revoked_at IS NULL AND revoked_reason IS NULL)",
    )
    op.drop_constraint("ck_authorization_bindings_revoker_type", _TABLE, type_="check")
    op.drop_column(_TABLE, "revoked_by_id")
    op.drop_column(_TABLE, "revoked_by_type")
