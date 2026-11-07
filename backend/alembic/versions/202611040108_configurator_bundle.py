"""Permit explicit Configurator bindings without granting any authority.

The literal vocabulary keeps every existing bundle. No bindings, identities
or sessions change when this structural migration runs.
"""

from __future__ import annotations

from alembic import op

revision = "202611040108"
down_revision = "202611030002"
branch_labels = None
depends_on = None

_TABLE = "authorization_bindings"
_CONSTRAINT = "ck_authorization_bindings_role_bundle"
_BEFORE = (
    "member",
    "viewer",
    "auditor",
    "security_officer",
    "compliance_officer",
    "analyst",
    "approver",
    "validator",
    "account_admin",
    "org_owner",
    "integration_writer",
    "bi_reader",
)
_AFTER = (*_BEFORE, "configurator")


def _apply(bundles: tuple[str, ...]) -> None:
    """Replace only the literal bundle CHECK, without changing stored authority."""
    values = ", ".join(f"'{bundle}'" for bundle in bundles)
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, f"role_bundle IN ({values})")


def upgrade() -> None:
    _apply(_AFTER)


def downgrade() -> None:
    # Fail while any Configurator row (including revoked history) exists.
    # Never silently erase authority or its audit history to narrow a CHECK.
    _apply(_BEFORE)
