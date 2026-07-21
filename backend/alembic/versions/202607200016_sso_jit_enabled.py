"""JIT provisioning flag on sso_connections.

Opt-in per connection: a verified IdP identity from an allowed email domain
that has no AequorOS account is auto-created as a read-only ``viewer``. The
service layer refuses to enable it without a non-empty domain allow-list.

Revision ID: 202607200016
Revises: 202607200015
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607200016"
down_revision = "202607200015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sso_connections",
        sa.Column("jit_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sso_connections", "jit_enabled")
