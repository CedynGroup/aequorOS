"""Give every active human an explicit baseline membership binding.

Revision ID: 202609160053
Revises: 202609160052

The ``member`` bundle carries no evaluator permissions. Its organization-wide
Account/restricted row is the auditable statement that an active human may load
the console shell, read their own profile/effective authority, use personal
self-service settings, and see the product module catalogue. Institution and
product access still require separate bindings.
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202609160053"
down_revision = "202609160052"
branch_labels = None
depends_on = None

_BINDINGS = "authorization_bindings"
_SYSTEM_GRANTOR = "migration:202609160053"
_LEGACY_SAFE_TENANT_CONTEXT = "00000000-0000-0000-0000-000000000000"
_GRANT_REASON = (
    "Baseline membership: allow an active organization member to load the console shell "
    "and use personal self-service without granting institution or product-module authority"
)
_FOUNDATION_BUNDLE_CHECK = (
    "role_bundle IN ('viewer', 'auditor', 'analyst', 'approver', 'account_admin', "
    "'org_owner', 'integration_writer')"
)
_MEMBERSHIP_BUNDLE_CHECK = (
    "role_bundle IN ('member', 'viewer', 'auditor', 'analyst', 'approver', "
    "'account_admin', 'org_owner', 'integration_writer')"
)


def _set_tenant(bind: sa.Connection, organization_id: str) -> None:
    bind.execute(
        sa.text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": organization_id},
    )


def _organization_ids(bind: sa.Connection) -> list[str]:
    with force_rls_suspended(bind, "organizations"):
        return [
            str(value)
            for value in bind.execute(sa.text("SELECT id FROM organizations ORDER BY id")).scalars()
        ]


def _upgrade_organization(
    bind: sa.Connection,
    *,
    organization_id: str,
    now: datetime,
) -> None:
    bind.execute(
        sa.text(
            f"""
            WITH candidates AS MATERIALIZED (
                SELECT id, COALESCE(display_name, email) AS principal_name
                FROM users
                WHERE organization_id = :organization_id
                  AND is_active IS TRUE
                  AND auth_provider <> 'service'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {_BINDINGS} AS existing
                      WHERE existing.organization_id = users.organization_id
                        AND existing.principal_user_id = users.id
                        AND existing.role_bundle = 'member'
                        AND existing.status = 'active'
                  )
                ORDER BY id
                FOR NO KEY UPDATE
            ),
            inserted AS (
                INSERT INTO {_BINDINGS}
                    (id, organization_id, principal_user_id, principal_type, role_bundle,
                     institution_scope, institution_id, module_scope, sensitivity_scope,
                     granted_by_type, granted_by_id, grant_reason, granted_at, status,
                     valid_from, valid_until, revoked_at, revoked_by_type, revoked_by_id,
                     revoked_reason, created_at, updated_at)
                SELECT gen_random_uuid(), :organization_id, candidates.id, 'human', 'member',
                       'organization', NULL, 'account', 'restricted',
                       'system', :grantor, :reason, :now, 'active',
                       :now, NULL, NULL, NULL, NULL, NULL, :now, :now
                FROM candidates
                RETURNING id, organization_id, principal_user_id, granted_at
            )
            INSERT INTO audit_events
                (id, organization_id, actor_user_id, event_type, entity_type,
                 entity_id, details, created_at)
            SELECT gen_random_uuid(), inserted.organization_id, NULL,
                   'authorization.binding_granted', 'authorization_binding',
                   inserted.id::text,
                   json_build_object(
                       'grantor_type', 'system',
                       'grantor_id', :grantor,
                       'grantee_user_id', inserted.principal_user_id::text,
                       'role_bundle', 'member',
                       'scope', json_build_object(
                           'institution_scope', 'organization',
                           'institution_id', NULL,
                           'module_scope', 'account',
                           'sensitivity_scope', 'restricted'
                       ),
                       'occurred_at', inserted.granted_at::text,
                       'reason', :reason,
                       'authority_sentence',
                           candidates.principal_name ||
                           ' is an active organization member with console ' ||
                           'and personal self-service access only.'
                   ),
                   :now
            FROM inserted
            JOIN candidates ON candidates.id = inserted.principal_user_id
            """
        ),
        {
            "organization_id": organization_id,
            "grantor": _SYSTEM_GRANTOR,
            "reason": _GRANT_REASON,
            "now": now,
        },
    )
    bind.execute(
        sa.text(
            """
            UPDATE users
            SET authorization_version = authorization_version + 1
            WHERE organization_id = :organization_id
              AND EXISTS (
                  SELECT 1
                  FROM authorization_bindings AS membership
                  WHERE membership.organization_id = users.organization_id
                    AND membership.principal_user_id = users.id
                    AND membership.role_bundle = 'member'
                    AND membership.status = 'active'
                    AND membership.granted_by_id = :grantor
              )
            """
        ),
        {
            "organization_id": organization_id,
            "grantor": _SYSTEM_GRANTOR,
        },
    )
    bind.execute(
        sa.text(
            """
            UPDATE refresh_tokens
            SET revoked_at = :now, revoked_reason = 'authorization_changed'
            WHERE organization_id = :organization_id
              AND revoked_at IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM authorization_bindings AS membership
                  WHERE membership.organization_id = refresh_tokens.organization_id
                    AND membership.principal_user_id = refresh_tokens.user_id
                    AND membership.role_bundle = 'member'
                    AND membership.status = 'active'
                    AND membership.granted_by_id = :grantor
              )
            """
        ),
        {
            "organization_id": organization_id,
            "grantor": _SYSTEM_GRANTOR,
            "now": now,
        },
    )


def _downgrade_organization(
    bind: sa.Connection,
    *,
    organization_id: str,
    now: datetime,
) -> None:
    bind.execute(
        sa.text(
            f"""
            UPDATE users
            SET authorization_version = authorization_version + 1
            WHERE organization_id = :organization_id
              AND EXISTS (
                  SELECT 1
                  FROM {_BINDINGS} AS membership
                  WHERE membership.organization_id = users.organization_id
                    AND membership.principal_user_id = users.id
                    AND membership.role_bundle = 'member'
              )
            """
        ),
        {"organization_id": organization_id},
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE refresh_tokens
            SET revoked_at = :now, revoked_reason = 'authorization_changed'
            WHERE organization_id = :organization_id
              AND revoked_at IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM {_BINDINGS} AS membership
                  WHERE membership.organization_id = refresh_tokens.organization_id
                    AND membership.principal_user_id = refresh_tokens.user_id
                    AND membership.role_bundle = 'member'
              )
            """
        ),
        {"organization_id": organization_id, "now": now},
    )
    bind.execute(
        sa.text(
            f"""
            DELETE FROM {_BINDINGS}
            WHERE organization_id = :organization_id
              AND role_bundle = 'member'
            """
        ),
        {"organization_id": organization_id},
    )


def upgrade() -> None:
    bind = op.get_bind()
    op.drop_constraint("ck_authorization_bindings_role_bundle", _BINDINGS, type_="check")
    op.create_check_constraint(
        "ck_authorization_bindings_role_bundle",
        _BINDINGS,
        _MEMBERSHIP_BUNDLE_CHECK,
    )
    op.create_index(
        "uq_authorization_bindings_active_member",
        _BINDINGS,
        ["organization_id", "principal_user_id"],
        unique=True,
        postgresql_where=sa.text("role_bundle = 'member' AND status = 'active'"),
    )

    now = datetime.now(UTC)
    organization_ids = _organization_ids(bind)
    with force_rls_suspended(
        bind,
        _BINDINGS,
        "audit_events",
        "refresh_tokens",
        "users",
    ):
        for organization_id in organization_ids:
            _set_tenant(bind, organization_id)
            _upgrade_organization(
                bind,
                organization_id=organization_id,
                now=now,
            )


def downgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    organization_ids = _organization_ids(bind)
    with force_rls_suspended(bind, _BINDINGS, "refresh_tokens", "users"):
        for organization_id in organization_ids:
            _set_tenant(bind, organization_id)
            _downgrade_organization(
                bind,
                organization_id=organization_id,
                now=now,
            )

    op.drop_index("uq_authorization_bindings_active_member", table_name=_BINDINGS)
    op.drop_constraint("ck_authorization_bindings_role_bundle", _BINDINGS, type_="check")
    op.create_check_constraint(
        "ck_authorization_bindings_role_bundle",
        _BINDINGS,
        _FOUNDATION_BUNDLE_CHECK,
    )
    # Alembic runs the full downgrade chain in one transaction. This migration
    # uses platform IDs as the tenant GUC, but pre-epoch RLS policies cast that
    # GUC to UUID. Leave a valid, non-matching UUID for those older revisions.
    _set_tenant(bind, _LEGACY_SAFE_TENANT_CONTEXT)
