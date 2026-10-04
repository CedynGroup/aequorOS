"""Revoke unsupported narrowed grants before enforcing Credit-only narrowing.

Deploy this migration before the application change. Non-Credit scopes cannot
be applied to institution figures. Revoke those rows, retaining their original
scope and recording the system revocation; never turn them into whole-book
grants. Invalidate each affected principal's sessions once in the same
transaction. If an unsupported grant belongs to a machine identity, revoke its
keys and all machine bindings and deactivate it, preserving the key lifecycle
invariant. Other whole-book grants, Credit grants and keys are unchanged.
Downgrade removes the constraint but never resurrects access.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610040083"
down_revision = "202609300082"
branch_labels = None
depends_on = None

TABLE = "authorization_bindings"
CONSTRAINT = "ck_authorization_bindings_narrowed_module"


def upgrade() -> None:
    bind = op.get_bind()
    with force_rls_suspended(bind, TABLE, "users", "refresh_tokens", "integration_keys"):
        principals = bind.execute(
            sa.text(
                "SELECT DISTINCT organization_id, principal_user_id, principal_type "
                "FROM authorization_bindings WHERE data_scope_kind <> 'all' "
                "AND module_scope <> 'credit' AND status <> 'revoked'"
            )
        ).all()
        # Lock the same identity rows as token issuance before changing authority.
        for organization_id, principal_id, _principal_type in sorted(principals):
            bind.execute(
                sa.text(
                    "SELECT id FROM users WHERE organization_id = :org AND id = :id FOR UPDATE"
                ),
                {"org": organization_id, "id": principal_id},
            )
        bind.execute(
            sa.text(
                "UPDATE authorization_bindings SET status = 'revoked', "
                "revoked_at = CURRENT_TIMESTAMP, revoked_by_type = 'system', "
                "revoked_by_id = 'migration:202610040083', "
                "revoked_reason = 'Branch and region narrowing is supported only for Credit', "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE data_scope_kind <> 'all' AND module_scope <> 'credit' "
                "AND status <> 'revoked'"
            )
        )
        for organization_id, principal_id, principal_type in principals:
            params = {"org": organization_id, "id": principal_id}
            if principal_type == "machine":
                bind.execute(
                    sa.text(
                        "UPDATE integration_keys SET revoked_at = CURRENT_TIMESTAMP, "
                        "updated_at = CURRENT_TIMESTAMP WHERE organization_id = :org "
                        "AND service_user_id = :id AND revoked_at IS NULL"
                    ),
                    params,
                )
                bind.execute(
                    sa.text(
                        "UPDATE users SET is_active = false "
                        "WHERE organization_id = :org AND id = :id AND auth_provider = 'service'"
                    ),
                    params,
                )
                bind.execute(
                    sa.text(
                        "UPDATE authorization_bindings SET status = 'revoked', "
                        "revoked_at = CURRENT_TIMESTAMP, revoked_by_type = 'system', "
                        "revoked_by_id = 'migration:202610040083', "
                        "revoked_reason = 'Machine credential held an unsupported narrowed grant', "
                        "updated_at = CURRENT_TIMESTAMP WHERE organization_id = :org "
                        "AND principal_user_id = :id AND principal_type = 'machine' "
                        "AND status <> 'revoked'"
                    ),
                    params,
                )
            bind.execute(
                sa.text(
                    "UPDATE users SET authorization_version = authorization_version + 1 "
                    "WHERE organization_id = :org AND id = :id"
                ),
                params,
            )
            bind.execute(
                sa.text(
                    "UPDATE refresh_tokens SET revoked_at = CURRENT_TIMESTAMP, "
                    "revoked_reason = 'authorization_changed' "
                    "WHERE organization_id = :org AND user_id = :id AND revoked_at IS NULL"
                ),
                params,
            )
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "data_scope_kind = 'all' OR module_scope = 'credit' OR status = 'revoked'",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
