"""Admit ``credit`` as a module scope and mirror ``risk`` authority onto it.

Revision ID: 202609220067
Revises: 202609220066

Credit becomes its own authorization module (``Module.CREDIT`` /
``ModuleScope.CREDIT``): the BI catalogue declares every credit measure and
dimension under it, and the credit routes cut over to scoped bindings on it in
a later phase. Two things have to happen in the database for that to be true.

1. The CHECK constraint ``ck_authorization_bindings_module_scope`` has been a
   literal since ``202608250044`` and no migration has touched it, while the
   model derives it from ``tuple(ModuleScope)``. A ``credit`` grant would
   therefore pass the evaluator and fail the INSERT with a constraint violation
   surfaced as a 500 — the exact failure ``202609200065`` closed for the
   ``validator`` bundle. The constraint is widened here (drop + create, with
   the BEFORE list written out so the downgrade restores the historical shape).

2. Until today the dashboard showed credit under the ``risk`` module, so a
   ``risk`` binding is the only thing anyone has ever granted for it. Making
   credit its own module without carrying that authority over would silently
   remove credit from every user who holds it. So every ACTIVE, HUMAN, not
   yet expired ``risk`` binding is mirrored into a ``credit`` sibling that
   copies the whole sentence — principal, bundle, institution scope and id,
   sensitivity, validity — with ``granted_by_type='system'`` and a reason that
   names this migration and the source row. A principal already covered by an
   equivalent ``credit`` or ``all`` row (same bundle, same or organization-wide
   institution coverage, same or ``all`` sensitivity, and not past its
   ``valid_until`` — an expired row grants nothing, so it masks nothing) is
   skipped, as are machine principals and the account-plane bundles
   (``member``, ``account_admin``, ``org_owner``), which carry no product
   module at all.

Every mirrored principal has their authorization version bumped and every
refresh family revoked in the same transaction (precedent ``202608280046``),
and every grant writes the standard ``authorization.binding_granted`` audit
row (precedent ``202609160053``) — a system grant is still a grant.

``authorization_bindings``, ``users``, ``refresh_tokens`` and ``audit_events``
are FORCE-RLS and Alembic runs as the tenant-scoped role, so every statement
sits inside ``force_rls_suspended`` naming all four; the per-organization GUC
is set as well, as ``202609160053`` does.

Nobody holds a ``risk`` binding that any backend route consumes today, so
this is a visibility-preserving mirror, not an authority transfer. Run
``scripts/authorization_access_impact.py`` before and after and diff the
product-view column.
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202609220067"
down_revision = "202609220066"
branch_labels = None
depends_on = None

_BINDINGS = "authorization_bindings"
_CONSTRAINT = "ck_authorization_bindings_module_scope"
_SYSTEM_GRANTOR = "migration:202609220067"
_REASON_PREFIX = "mirrored by migration 202609220067 from risk"
_LEGACY_SAFE_TENANT_CONTEXT = "00000000-0000-0000-0000-000000000000"

#: The scopes the constraint accepted before ``credit`` existed
#: (``202608250044:132-137``). Written out rather than derived, so the
#: downgrade restores the historical shape even after the enum grows again.
_MODULES_BEFORE: tuple[str, ...] = (
    "all",
    "liq",
    "cap",
    "irrbb",
    "fx",
    "ftp",
    "fcst",
    "beh",
    "data",
    "reg",
    "risk",
    "markets",
    "account",
    "audit",
)
#: Deliberately a literal, not ``ModuleScope``: the model derives its own
#: CHECK from the enum, and the Postgres parity test proves the two agree.
_MODULES_AFTER: tuple[str, ...] = (*_MODULES_BEFORE, "credit")

#: Bundles that carry no product module and are never mirrored.
_ACCOUNT_PLANE_BUNDLES = "'member', 'account_admin', 'org_owner', 'integration_writer'"


def _check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"module_scope IN ({joined})"


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


def _mirror_organization(bind: sa.Connection, *, organization_id: str, now: datetime) -> None:
    bind.execute(
        sa.text(
            f"""
            WITH source AS MATERIALIZED (
                SELECT b.id, b.organization_id, b.principal_user_id, b.role_bundle,
                       b.institution_scope, b.institution_id, b.sensitivity_scope,
                       b.grant_reason, b.valid_from, b.valid_until,
                       COALESCE(u.display_name, u.email) AS principal_name
                  FROM {_BINDINGS} AS b
                  JOIN users AS u
                    ON u.id = b.principal_user_id
                   AND u.organization_id = b.organization_id
                 WHERE b.organization_id = :organization_id
                   AND b.module_scope = 'risk'
                   AND b.status = 'active'
                   AND b.principal_type = 'human'
                   AND u.auth_provider <> 'service'
                   AND b.role_bundle NOT IN ({_ACCOUNT_PLANE_BUNDLES})
                   AND (b.valid_until IS NULL OR b.valid_until > :now)
                   AND NOT EXISTS (
                       SELECT 1
                         FROM {_BINDINGS} AS existing
                        WHERE existing.organization_id = b.organization_id
                          AND existing.principal_user_id = b.principal_user_id
                          AND existing.status = 'active'
                          -- Coverage the evaluator honours: an expired row
                          -- (``valid_until`` past, status still active) grants
                          -- nothing and must not mask the mirror (audit A4-04).
                          -- A future ``valid_from`` still counts — the cover
                          -- row starts when the source row's copy would.
                          AND (existing.valid_until IS NULL OR existing.valid_until > :now)
                          AND existing.module_scope IN ('credit', 'all')
                          AND existing.role_bundle = b.role_bundle
                          AND (
                              existing.institution_scope = 'organization'
                              OR (
                                  existing.institution_scope = b.institution_scope
                                  AND existing.institution_id IS NOT DISTINCT FROM b.institution_id
                              )
                          )
                          AND existing.sensitivity_scope IN ('all', b.sensitivity_scope)
                   )
                 ORDER BY b.id
            ),
            inserted AS (
                INSERT INTO {_BINDINGS}
                    (id, organization_id, principal_user_id, principal_type, role_bundle,
                     institution_scope, institution_id, module_scope, sensitivity_scope,
                     granted_by_type, granted_by_id, grant_reason, granted_at, status,
                     valid_from, valid_until, revoked_at, revoked_by_type, revoked_by_id,
                     revoked_reason, created_at, updated_at)
                SELECT gen_random_uuid(), source.organization_id, source.principal_user_id,
                       'human', source.role_bundle,
                       source.institution_scope, source.institution_id, 'credit',
                       source.sensitivity_scope,
                       'system', :grantor,
                       :reason_prefix || ' binding ' || source.id::text || ': '
                           || source.grant_reason,
                       :now, 'active',
                       source.valid_from, source.valid_until, NULL, NULL, NULL,
                       NULL, :now, :now
                  FROM source
                RETURNING id, organization_id, principal_user_id, role_bundle,
                          institution_scope, institution_id, sensitivity_scope,
                          grant_reason, granted_at
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
                       'role_bundle', inserted.role_bundle,
                       'scope', json_build_object(
                           'institution_scope', inserted.institution_scope,
                           'institution_id', inserted.institution_id,
                           'module_scope', 'credit',
                           'sensitivity_scope', inserted.sensitivity_scope
                       ),
                       'occurred_at', inserted.granted_at::text,
                       'reason', inserted.grant_reason,
                       'authority_sentence',
                           source.principal_name || ' holds the ' || inserted.role_bundle
                           || ' bundle for the credit module ('
                           || inserted.sensitivity_scope || ' sensitivity, '
                           || CASE WHEN inserted.institution_scope = 'organization'
                                   THEN 'organization-wide'
                                   ELSE 'institution ' || inserted.institution_id END
                           || '), mirrored from the equivalent risk grant by '
                           || 'migration 202609220067.'
                   ),
                   :now
              FROM inserted
              JOIN source
                ON inserted.grant_reason
                   = :reason_prefix || ' binding ' || source.id::text || ': ' || source.grant_reason
            """
        ),
        {
            "organization_id": organization_id,
            "grantor": _SYSTEM_GRANTOR,
            "reason_prefix": _REASON_PREFIX,
            "now": now,
        },
    )


def _invalidate_mirrored_principals(
    bind: sa.Connection, *, organization_id: str, now: datetime
) -> None:
    """Bump ``authv`` and end every refresh family of principals holding a
    ``credit`` binding granted by THIS migration — the same two statements a
    live grant runs through ``invalidate_user_authorization``."""
    bind.execute(
        sa.text(
            f"""
            UPDATE users
            SET authorization_version = authorization_version + 1
            WHERE organization_id = :organization_id
              AND EXISTS (
                  SELECT 1
                  FROM {_BINDINGS} AS mirrored
                  WHERE mirrored.organization_id = users.organization_id
                    AND mirrored.principal_user_id = users.id
                    AND mirrored.module_scope = 'credit'
                    AND mirrored.granted_by_id = :grantor
              )
            """
        ),
        {"organization_id": organization_id, "grantor": _SYSTEM_GRANTOR},
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
                  FROM {_BINDINGS} AS mirrored
                  WHERE mirrored.organization_id = refresh_tokens.organization_id
                    AND mirrored.principal_user_id = refresh_tokens.user_id
                    AND mirrored.module_scope = 'credit'
                    AND mirrored.granted_by_id = :grantor
              )
            """
        ),
        {"organization_id": organization_id, "grantor": _SYSTEM_GRANTOR, "now": now},
    )


def _unmirror_organization(bind: sa.Connection, *, organization_id: str, now: datetime) -> None:
    # Sessions of anyone holding a credit row end with the row, mirrored or
    # not: the narrowed constraint cannot admit ANY ``credit`` value, so every
    # such row has to go before it is restored (the ``202609200065`` shape).
    bind.execute(
        sa.text(
            f"""
            UPDATE users
            SET authorization_version = authorization_version + 1
            WHERE organization_id = :organization_id
              AND EXISTS (
                  SELECT 1
                  FROM {_BINDINGS} AS credit
                  WHERE credit.organization_id = users.organization_id
                    AND credit.principal_user_id = users.id
                    AND credit.module_scope = 'credit'
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
                  FROM {_BINDINGS} AS credit
                  WHERE credit.organization_id = refresh_tokens.organization_id
                    AND credit.principal_user_id = refresh_tokens.user_id
                    AND credit.module_scope = 'credit'
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
              AND module_scope = 'credit'
              AND granted_by_id = :grantor
              AND grant_reason LIKE :reason_pattern
            """
        ),
        {
            "organization_id": organization_id,
            "grantor": _SYSTEM_GRANTOR,
            "reason_pattern": f"{_REASON_PREFIX}%",
        },
    )
    bind.execute(
        sa.text(
            f"DELETE FROM {_BINDINGS} "
            "WHERE organization_id = :organization_id AND module_scope = 'credit'"
        ),
        {"organization_id": organization_id},
    )


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _BINDINGS, type_="check")
    op.create_check_constraint(_CONSTRAINT, _BINDINGS, _check(_MODULES_AFTER))

    bind = op.get_bind()
    now = datetime.now(UTC)
    organization_ids = _organization_ids(bind)
    with force_rls_suspended(bind, _BINDINGS, "users", "refresh_tokens", "audit_events"):
        for organization_id in organization_ids:
            _set_tenant(bind, organization_id)
            _mirror_organization(bind, organization_id=organization_id, now=now)
            _invalidate_mirrored_principals(bind, organization_id=organization_id, now=now)
    # Leave no tenant selected for whatever runs next in this transaction.
    _set_tenant(bind, "")


def downgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    organization_ids = _organization_ids(bind)
    with force_rls_suspended(bind, _BINDINGS, "users", "refresh_tokens"):
        for organization_id in organization_ids:
            _set_tenant(bind, organization_id)
            _unmirror_organization(bind, organization_id=organization_id, now=now)

    op.drop_constraint(_CONSTRAINT, _BINDINGS, type_="check")
    op.create_check_constraint(_CONSTRAINT, _BINDINGS, _check(_MODULES_BEFORE))
    # Alembic runs the full downgrade chain in one transaction. This migration
    # uses platform IDs as the tenant GUC, but pre-epoch RLS policies cast that
    # GUC to UUID. Leave a valid, non-matching UUID for those older revisions.
    _set_tenant(bind, _LEGACY_SAFE_TENANT_CONTEXT)
