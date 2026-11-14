"""Permit explicit read-only Exporter bindings without granting any authority.

The literal vocabulary keeps every existing bundle. No bindings, identities or
sessions change when this structural migration runs: it widens the CHECK that
decides which values the column accepts, and inserts nothing — the exporter
role becomes real only when an administrator grants it from the Members
composer. The same shape as the signer bundle migration.
"""

from __future__ import annotations

from alembic import op

revision = "202611140118"
down_revision = "202611090117"
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
    "configurator",
    "signer",
    "account_admin",
    "org_owner",
    "integration_writer",
    "bi_reader",
)
_AFTER = (*_BEFORE, "exporter")


def _apply(bundles: tuple[str, ...]) -> None:
    """Replace only the literal bundle CHECK, without changing stored authority."""
    values = ", ".join(f"'{bundle}'" for bundle in bundles)
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, f"role_bundle IN ({values})")


def upgrade() -> None:
    _apply(_AFTER)


def downgrade() -> None:
    # Fail while any Exporter row (including revoked history) exists.
    # Never silently erase authority or its audit history to narrow a CHECK.
    _apply(_BEFORE)
