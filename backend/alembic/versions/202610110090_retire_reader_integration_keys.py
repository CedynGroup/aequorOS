"""Revoke analytics credentials until governed BI issuance ships.

Apply before deploying writer-only issuance, with key issuance suspended for
the rollout. No existing reader authority is converted into writer authority.
Historical rows remain inspectable; downgrade never restores revoked secrets.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610110090"
down_revision = "202610100090"
branch_labels = None
depends_on = None

_REASON = "Analytics reader credentials retired pending governed BI issuance"


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    with force_rls_suspended(
        bind, "authorization_bindings", "integration_keys", "users", "refresh_tokens"
    ):
        # Include orphan bindings and previously revoked reader bindings: their
        # dedicated identity and any other live credentials must also be ended.
        principals = cast(
            list[tuple[str, UUID]],
            bind.execute(
                sa.text(
                    """
                    SELECT u.organization_id, u.id FROM users AS u
                    WHERE EXISTS (
                        SELECT 1 FROM authorization_bindings AS b
                        WHERE b.organization_id = u.organization_id
                          AND b.principal_user_id = u.id
                          AND b.principal_type = 'machine'
                          AND b.role_bundle = 'bi_reader'
                    )
                    ORDER BY u.organization_id, u.id
                    FOR NO KEY UPDATE OF u
                    """
                )
            )
            .tuples()
            .all(),
        )
        for organization_id, user_id in principals:
            params = {
                "organization_id": organization_id,
                "user_id": user_id,
                "now": now,
                "reason": _REASON,
                "migration": f"migration:{revision}",
            }
            bind.execute(
                sa.text(
                    """
                    UPDATE integration_keys SET revoked_at = :now, updated_at = :now
                    WHERE organization_id = :organization_id
                      AND service_user_id = :user_id AND revoked_at IS NULL
                    """
                ),
                params,
            )
            bind.execute(
                sa.text(
                    """
                    UPDATE authorization_bindings
                    SET status = 'revoked', revoked_at = :now,
                        revoked_by_type = 'system', revoked_by_id = :migration,
                        revoked_reason = :reason, updated_at = :now
                    WHERE organization_id = :organization_id
                      AND principal_user_id = :user_id AND principal_type = 'machine'
                      AND status <> 'revoked'
                    """
                ),
                params,
            )
            # Migration equivalent of invalidate_user_authorization: same user
            # lock, version increment and refresh-family invalidation transaction.
            bind.execute(
                sa.text(
                    """
                    UPDATE users SET is_active = false,
                        authorization_version = authorization_version + 1, updated_at = :now
                    WHERE organization_id = :organization_id AND id = :user_id
                    """
                ),
                params,
            )
            bind.execute(
                sa.text(
                    """
                    UPDATE refresh_tokens
                    SET revoked_at = :now, revoked_reason = 'authorization_changed',
                        updated_at = :now
                    WHERE organization_id = :organization_id AND user_id = :user_id
                      AND revoked_at IS NULL
                    """
                ),
                params,
            )


def downgrade() -> None:
    """Retain revocations and invalidation; rollback cannot restore authority."""
