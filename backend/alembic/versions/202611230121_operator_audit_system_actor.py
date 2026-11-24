"""Let the staff log record the staff plane's own scheduled acts.

Revision ID: 202611230121
Revises: 202611210120

Widens ``ck_operator_audit_log_auth_mode`` to include ``system``: the scheduled
gateway certificate check renews certificates with no staff member signed in, and its
renewals and failures belong in the same append-only staff log as the console's.
``operator_audit_log`` is a global control-plane table without row-level security, and
re-creating the CHECK validates existing rows without writing any.
"""

from __future__ import annotations

from alembic import op

revision = "202611230121"
down_revision = "202611210120"
branch_labels = None
depends_on = None

_NAME = "ck_operator_audit_log_auth_mode"
_TABLE = "operator_audit_log"


def _recreate(modes: tuple[str, ...]) -> None:
    op.drop_constraint(_NAME, _TABLE, type_="check")
    op.create_check_constraint(
        _NAME, _TABLE, "auth_mode IN (" + ", ".join(f"'{mode}'" for mode in modes) + ")"
    )


def upgrade() -> None:
    _recreate(("dev", "oidc", "password", "system"))


def downgrade() -> None:
    # Refuses while a system row exists: the staff log is append-only, so those rows
    # cannot be removed to make room for the narrower rule.
    _recreate(("dev", "oidc", "password"))
