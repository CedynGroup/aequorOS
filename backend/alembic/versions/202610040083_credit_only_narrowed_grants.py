"""Limit stored branch and region scopes to Credit."""

from __future__ import annotations

from alembic import op

revision = "202610040083"
down_revision = "202609300082"
branch_labels = None
depends_on = None

TABLE = "authorization_bindings"
CONSTRAINT = "ck_authorization_bindings_narrowed_module"


def upgrade() -> None:
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "data_scope_kind = 'all' OR module_scope = 'credit'",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
