"""Give every Org Owner an explicit organization-wide read sentence.

Revision ID: 202609160052
Revises: 202609110051

The Org Owner bundle carries ``administer`` only and the owner binding is
written on the Account module alone, so once the dashboard derived visibility
from bindings (2026-09-08) an Owner holding nothing else saw no institution and
no module. docs/rbac.md §7 gives an Owner ``view`` on every module "for
administration context but no operational write"; the build never wrote that
sentence.

This revision writes it: for every effective human ``org_owner`` binding whose
user is active, one ``viewer`` / organization-wide / ``all`` modules / ``all``
sensitivities binding, unless the owner already holds an equivalent effective
organization-wide all/all row of a view-carrying bundle (viewer, auditor,
analyst, approver). It grants no create, run, approve, submit, billing,
transfer or deletion authority — SoD C9 keeps maker/checker authority away
from account administration — and it never creates or moves ownership.

For each binding actually inserted, the same transaction takes the user lock
used by token issuance, advances ``authorization_version``, and revokes every
live refresh-token family with ``authorization_changed``. Affected owners must
sign in again. Owners skipped as already covered are not invalidated. The
provisioning saga writes the same second sentence for new tenants
(``services/organization_ownership.py``), so this backfill is one-time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202609160052"
down_revision = "202609110051"
branch_labels = None
depends_on = None

_BINDINGS = "authorization_bindings"
_SYSTEM_GRANTOR = "migration:202609160052"
_GRANT_REASON = (
    "Org Owner read access: the account owner sees every module read-only for "
    "administration context (docs/rbac.md §7 permission matrix); operational "
    "maker/checker authority stays separate (SoD C9)"
)


def _owners_requiring_read_access(bind: sa.Connection, now: datetime) -> list[tuple[str, UUID]]:
    rows = bind.execute(
        sa.text(
            f"""
            SELECT owner.organization_id,
                   owner.principal_user_id
            FROM {_BINDINGS} AS owner
            JOIN users AS principal
              ON principal.organization_id = owner.organization_id
             AND principal.id = owner.principal_user_id
            WHERE owner.role_bundle = 'org_owner'
              AND owner.principal_type = 'human'
              AND owner.status = 'active'
              AND owner.revoked_at IS NULL
              AND owner.valid_from <= :now
              AND (owner.valid_until IS NULL OR owner.valid_until > :now)
              AND principal.is_active IS TRUE
              AND principal.auth_provider <> 'service'
              AND NOT EXISTS (
                  SELECT 1
                  FROM {_BINDINGS} AS existing
                  WHERE existing.organization_id = owner.organization_id
                    AND existing.principal_user_id = owner.principal_user_id
                    AND existing.principal_type = 'human'
                    AND existing.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
                    AND existing.institution_scope = 'organization'
                    AND existing.institution_id IS NULL
                    AND existing.module_scope = 'all'
                    AND existing.sensitivity_scope = 'all'
                    AND existing.status = 'active'
                    AND existing.revoked_at IS NULL
                    AND existing.valid_from <= :now
                    AND (existing.valid_until IS NULL OR existing.valid_until > :now)
              )
            ORDER BY owner.organization_id, owner.principal_user_id
            FOR NO KEY UPDATE OF principal
            """
        ),
        {"now": now},
    )
    return [(str(row.organization_id), UUID(str(row.principal_user_id))) for row in rows]


def _insert_read_binding(
    bind: sa.Connection,
    *,
    organization_id: str,
    principal_user_id: UUID,
    now: datetime,
) -> None:
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {_BINDINGS}
                (id, organization_id, principal_user_id, principal_type, role_bundle,
                 institution_scope, institution_id, module_scope, sensitivity_scope,
                 granted_by_type, granted_by_id, grant_reason, granted_at, status,
                 valid_from, valid_until, revoked_at, revoked_by_type, revoked_by_id,
                 revoked_reason, created_at, updated_at)
            VALUES
                (:id, :organization_id, :principal_user_id, 'human', 'viewer',
                 'organization', NULL, 'all', 'all',
                 'system', :granted_by_id, :grant_reason, :now, 'active',
                 :now, NULL, NULL, NULL, NULL, NULL, :now, :now)
            """
        ),
        {
            "id": uuid4(),
            "organization_id": organization_id,
            "principal_user_id": principal_user_id,
            "granted_by_id": _SYSTEM_GRANTOR,
            "grant_reason": _GRANT_REASON,
            "now": now,
        },
    )


def _invalidate_user(
    bind: sa.Connection,
    *,
    organization_id: str,
    principal_user_id: UUID,
    now: datetime,
) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE users
            SET authorization_version = authorization_version + 1
            WHERE organization_id = :organization_id
              AND id = :principal_user_id
            """
        ),
        {
            "organization_id": organization_id,
            "principal_user_id": principal_user_id,
        },
    )
    bind.execute(
        sa.text(
            """
            UPDATE refresh_tokens
            SET revoked_at = :now, revoked_reason = 'authorization_changed'
            WHERE user_id = :principal_user_id
              AND organization_id = :organization_id
              AND revoked_at IS NULL
            """
        ),
        {
            "organization_id": organization_id,
            "principal_user_id": principal_user_id,
            "now": now,
        },
    )


def upgrade() -> None:
    now = datetime.now(UTC)
    bind = op.get_bind()
    with force_rls_suspended(bind, _BINDINGS, "users", "refresh_tokens"):
        for organization_id, principal_user_id in _owners_requiring_read_access(bind, now):
            _insert_read_binding(
                bind,
                organization_id=organization_id,
                principal_user_id=principal_user_id,
                now=now,
            )
            _invalidate_user(
                bind,
                organization_id=organization_id,
                principal_user_id=principal_user_id,
                now=now,
            )


def downgrade() -> None:
    now = datetime.now(UTC)
    bind = op.get_bind()
    with force_rls_suspended(bind, _BINDINGS, "users", "refresh_tokens"):
        principals = [
            (str(row.organization_id), UUID(str(row.principal_user_id)))
            for row in bind.execute(
                sa.text(
                    f"""
                    SELECT principal.organization_id,
                           principal.id AS principal_user_id
                    FROM users AS principal
                    WHERE EXISTS (
                        SELECT 1
                        FROM {_BINDINGS} AS binding
                        WHERE binding.organization_id = principal.organization_id
                          AND binding.principal_user_id = principal.id
                          AND binding.granted_by_type = 'system'
                          AND binding.granted_by_id = :granted_by_id
                    )
                    ORDER BY principal.organization_id, principal.id
                    FOR NO KEY UPDATE OF principal
                    """
                ),
                {"granted_by_id": _SYSTEM_GRANTOR},
            )
        ]
        bind.execute(
            sa.text(
                f"""
                DELETE FROM {_BINDINGS}
                WHERE granted_by_type = 'system'
                  AND granted_by_id = :granted_by_id
                """
            ),
            {"granted_by_id": _SYSTEM_GRANTOR},
        )
        for organization_id, principal_user_id in principals:
            _invalidate_user(
                bind,
                organization_id=organization_id,
                principal_user_id=principal_user_id,
                now=now,
            )
