"""Restore legacy account administration without guessing organization ownership.

Revision ID: 202609090051
Revises: 202608290047

Migration ``202608280046`` correctly refused to choose an Org Owner when an
organization had zero or multiple eligible active human legacy administrators.
It preserved that decision, and the exact eligible-candidate snapshot, in
``organization_owner_assignments``. This revision consumes that recorded
snapshot together with ``initial_admin_role_demotions`` rather than defining a
second candidate rule that could drift.

Every still-active human candidate in an unresolved multi-candidate
organization receives the least-privilege organization-scoped
``account_admin`` / ``account`` / ``restricted`` binding unless the organization
already has an owner or the candidate already has suitable Account
administration authority. This is not an escalation: it restores exactly the
account-plane authority those administrators exercised under the legacy
``admin`` role, without granting billing, transfer, deletion, or Org Owner grant
administration. Every binding records the migration as grantor and explains the
compatibility restoration.

Ownership remains unresolved by design. No ``org_owner`` binding is created and
every ``designation_required`` record survives unchanged. Until staff designate
an owner, these organizations still cannot create new grants. Account
administration also does not imply organization-directory view, which remains a
separate institution-approved binding.

For each binding actually inserted, the same transaction takes the user lock
used by token issuance, advances ``authorization_version``, and revokes every
live refresh-token family with ``authorization_changed``. Affected users must
sign in again. Organizations and users skipped as already authorized are not
invalidated.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202609090051"
down_revision = "202608290047"
branch_labels = None
depends_on = None

_ASSIGNMENTS = "organization_owner_assignments"
_BINDINGS = "authorization_bindings"
_ROLE_DEMOTIONS = "initial_admin_role_demotions"
_SYSTEM_GRANTOR = "migration:202609090051"
_GRANT_REASON = (
    "Restore legacy account administration for an eligible administrator while "
    "Org Owner designation remains outstanding"
)


def _candidates_requiring_binding(bind: sa.Connection, now: datetime) -> list[tuple[str, UUID]]:
    rows = bind.execute(
        sa.text(
            f"""
            SELECT assignment.organization_id,
                   principal.id AS principal_user_id
            FROM {_ASSIGNMENTS} AS assignment
            CROSS JOIN LATERAL
                json_array_elements(assignment.eligible_candidates) AS candidate(snapshot)
            JOIN users AS principal
              ON principal.organization_id = assignment.organization_id
             AND principal.id = (candidate.snapshot ->> 'user_id')::uuid
            JOIN {_ROLE_DEMOTIONS} AS demotion
              ON demotion.organization_id = principal.organization_id
             AND demotion.user_id = principal.id
            WHERE assignment.status = 'designation_required'
              AND assignment.basis = 'multiple_eligible_active_human_administrators'
              AND assignment.owner_user_id IS NULL
              AND assignment.owner_binding_id IS NULL
              AND principal.role = 'account_admin'
              AND principal.is_active IS TRUE
              AND principal.auth_provider <> 'service'
              AND NOT EXISTS (
                  SELECT 1
                  FROM {_BINDINGS} AS owner
                  WHERE owner.organization_id = assignment.organization_id
                    AND owner.role_bundle = 'org_owner'
                    AND owner.status = 'active'
                    AND owner.revoked_at IS NULL
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM {_BINDINGS} AS existing
                  WHERE existing.organization_id = assignment.organization_id
                    AND existing.principal_user_id = principal.id
                    AND existing.principal_type = 'human'
                    AND existing.role_bundle IN ('account_admin', 'org_owner')
                    AND existing.institution_scope = 'organization'
                    AND existing.institution_id IS NULL
                    AND existing.module_scope IN ('account', 'all')
                    AND existing.sensitivity_scope IN ('restricted', 'all')
                    AND existing.status = 'active'
                    AND existing.revoked_at IS NULL
                    AND existing.valid_from <= :now
                    AND (existing.valid_until IS NULL OR existing.valid_until > :now)
              )
            ORDER BY assignment.organization_id, principal.id
            FOR NO KEY UPDATE OF principal
            """
        ),
        {"now": now},
    )
    return [(str(row.organization_id), UUID(str(row.principal_user_id))) for row in rows]


def _insert_binding(
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
                (:id, :organization_id, :principal_user_id, 'human', 'account_admin',
                 'organization', NULL, 'account', 'restricted',
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
    with force_rls_suspended(
        bind,
        _ASSIGNMENTS,
        _BINDINGS,
        _ROLE_DEMOTIONS,
        "users",
        "refresh_tokens",
    ):
        candidates = _candidates_requiring_binding(bind, now)
        for organization_id, principal_user_id in candidates:
            _insert_binding(
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
