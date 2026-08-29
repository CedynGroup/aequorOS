"""Assign initial Org Owners only when the eligible administrator is unambiguous.

Revision ID: 202608280046
Revises: 202608280045

Every organization receives a durable assignment-state row. Exactly one active
human legacy administrator becomes Org Owner through an auditable scoped
binding. Zero or multiple candidates receive no binding; their state row names
the reason and snapshots the candidates for later explicit staff designation.

The same transaction converts every legacy ``admin`` scalar role to the
account-plane-only ``account_admin`` value and advances its authorization
version. Outstanding refresh families are revoked, so no existing admin token
can retain the old analyst/approver rank after the migration commits.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping

from alembic import op
from app.db.session import force_rls_suspended

revision = "202608280046"
down_revision = "202608280045"
branch_labels = None
depends_on = None

_ASSIGNMENTS = "organization_owner_assignments"
_BINDINGS = "authorization_bindings"
_ROLE_DEMOTIONS = "initial_admin_role_demotions"
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"
_SYSTEM_GRANTOR = "migration:202608280046"
_AUTO_REASON = (
    "Initial Org Owner auto-assignment: exactly one eligible active human administrator existed"
)
_LEGACY_USER_ROLE_CHECK = "role IN ('admin', 'approver', 'analyst', 'examiner', 'viewer')"
_ACCOUNT_ADMIN_USER_ROLE_CHECK = (
    "role IN ('admin', 'account_admin', 'approver', 'analyst', 'examiner', 'viewer')"
)
_FOUNDATION_BUNDLE_CHECK = (
    "role_bundle IN ('viewer', 'auditor', 'analyst', 'approver', 'account_admin', "
    "'integration_writer')"
)
_OWNER_BUNDLE_CHECK = (
    "role_bundle IN ('viewer', 'auditor', 'analyst', 'approver', 'account_admin', "
    "'org_owner', 'integration_writer')"
)


def _candidate_snapshot(row: RowMapping) -> dict[str, str | None]:
    return {
        "user_id": str(row["id"]),
        "email": str(row["email"]),
        "display_name": None if row["display_name"] is None else str(row["display_name"]),
    }


def _backfill_assignment_states(bind: sa.Connection, now: datetime) -> None:
    organizations = list(
        bind.execute(sa.text("SELECT id FROM organizations ORDER BY id")).mappings()
    )
    insert_binding = sa.text(
        f"""
        INSERT INTO {_BINDINGS}
            (id, organization_id, principal_user_id, principal_type, role_bundle,
             institution_scope, institution_id, module_scope, sensitivity_scope,
             granted_by_type, granted_by_id, grant_reason, granted_at, status,
             valid_from, valid_until, revoked_at, revoked_reason, created_at, updated_at)
        VALUES
            (:id, :organization_id, :principal_user_id, 'human', 'org_owner',
             'organization', NULL, 'account', 'all', 'system', :granted_by_id,
             :grant_reason, :now, 'active', :now, NULL, NULL, NULL, :now, :now)
        """
    )
    assignment_table = sa.table(
        _ASSIGNMENTS,
        sa.column("organization_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("basis", sa.String()),
        sa.column("eligible_candidate_count", sa.Integer()),
        sa.column("eligible_candidates", sa.JSON()),
        sa.column("owner_user_id", sa.Uuid()),
        sa.column("owner_binding_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    for organization in organizations:
        organization_id = str(organization["id"])
        candidates = list(
            bind.execute(
                sa.text(
                    """
                    SELECT id, email, display_name
                    FROM users
                    WHERE organization_id = :organization_id
                      AND role = 'admin'
                      AND is_active IS TRUE
                      AND auth_provider <> 'service'
                    ORDER BY email, id
                    """
                ),
                {"organization_id": organization_id},
            ).mappings()
        )
        snapshots = [_candidate_snapshot(candidate) for candidate in candidates]
        count = len(candidates)
        owner_user_id = None
        owner_binding_id = None
        if count == 1:
            owner_user_id = candidates[0]["id"]
            owner_binding_id = uuid4()
            bind.execute(
                insert_binding,
                {
                    "id": owner_binding_id,
                    "organization_id": organization_id,
                    "principal_user_id": owner_user_id,
                    "granted_by_id": _SYSTEM_GRANTOR,
                    "grant_reason": _AUTO_REASON,
                    "now": now,
                },
            )
            status = "assigned"
            basis = "exactly_one_eligible_active_human_administrator"
        elif count == 0:
            status = "designation_required"
            basis = "zero_eligible_active_human_administrators"
        else:
            status = "designation_required"
            basis = "multiple_eligible_active_human_administrators"

        bind.execute(
            assignment_table.insert().values(
                organization_id=organization_id,
                status=status,
                basis=basis,
                eligible_candidate_count=count,
                eligible_candidates=snapshots,
                owner_user_id=owner_user_id,
                owner_binding_id=owner_binding_id,
                created_at=now,
                updated_at=now,
            )
        )


def upgrade() -> None:
    now = datetime.now(UTC)
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", _ACCOUNT_ADMIN_USER_ROLE_CHECK)
    op.drop_constraint("ck_authorization_bindings_role_bundle", _BINDINGS, type_="check")
    op.create_check_constraint(
        "ck_authorization_bindings_role_bundle", _BINDINGS, _OWNER_BUNDLE_CHECK
    )
    op.create_index(
        "uq_authorization_bindings_active_org_owner",
        _BINDINGS,
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("role_bundle = 'org_owner' AND status = 'active'"),
    )

    op.create_table(
        _ASSIGNMENTS,
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("basis", sa.String(length=64), nullable=False),
        sa.Column("eligible_candidate_count", sa.Integer(), nullable=False),
        sa.Column("eligible_candidates", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("owner_binding_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("organization_id"),
        sa.UniqueConstraint("owner_binding_id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="RESTRICT",
            name="fk_organization_owner_assignments_owner_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["owner_binding_id"],
            ["authorization_bindings.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('assigned', 'designation_required')",
            name="ck_organization_owner_assignments_status",
        ),
        sa.CheckConstraint(
            "basis IN ('exactly_one_eligible_active_human_administrator', "
            "'zero_eligible_active_human_administrators', "
            "'multiple_eligible_active_human_administrators', 'explicit_designation')",
            name="ck_organization_owner_assignments_basis",
        ),
        sa.CheckConstraint(
            "eligible_candidate_count >= 0",
            name="ck_organization_owner_assignments_candidate_count",
        ),
        sa.CheckConstraint(
            "(status = 'assigned' AND owner_user_id IS NOT NULL AND "
            "owner_binding_id IS NOT NULL) OR "
            "(status = 'designation_required' AND owner_user_id IS NULL AND "
            "owner_binding_id IS NULL)",
            name="ck_organization_owner_assignments_resolution",
        ),
        sa.CheckConstraint(
            "(basis = 'exactly_one_eligible_active_human_administrator' AND "
            "status = 'assigned' AND eligible_candidate_count = 1) OR "
            "(basis = 'zero_eligible_active_human_administrators' AND "
            "status = 'designation_required' AND eligible_candidate_count = 0) OR "
            "(basis = 'multiple_eligible_active_human_administrators' AND "
            "status = 'designation_required' AND eligible_candidate_count > 1) OR "
            "(basis = 'explicit_designation' AND status = 'assigned')",
            name="ck_organization_owner_assignments_basis_count",
        ),
    )
    op.create_index(
        "ix_organization_owner_assignments_status",
        _ASSIGNMENTS,
        ["status", "organization_id"],
    )
    op.create_table(
        _ROLE_DEMOTIONS,
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("demoted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="CASCADE",
            name="fk_initial_admin_role_demotions_user_tenant",
        ),
    )
    bind = op.get_bind()
    with force_rls_suspended(bind, "organizations", "users", "refresh_tokens", _BINDINGS):
        _backfill_assignment_states(bind, now)

        bind.execute(
            sa.text(
                f"""
                INSERT INTO {_ROLE_DEMOTIONS} (user_id, organization_id, demoted_at)
                SELECT id, organization_id, :now
                FROM users
                WHERE role = 'admin'
                """
            ),
            {"now": now},
        )

        # End every legacy admin session in the same transaction as the role
        # split. Already-revoked refresh rows keep their original evidence.
        bind.execute(
            sa.text(
                """
                UPDATE refresh_tokens
                SET revoked_at = :now, revoked_reason = 'authorization_changed'
                WHERE revoked_at IS NULL
                  AND user_id IN (SELECT id FROM users WHERE role = 'admin')
                """
            ),
            {"now": now},
        )
        bind.execute(
            sa.text(
                "UPDATE users SET role = 'account_admin', "
                "authorization_version = authorization_version + 1 WHERE role = 'admin'"
            )
        )

    op.execute(f"ALTER TABLE {_ASSIGNMENTS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_ASSIGNMENTS} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {_ASSIGNMENTS}_tenant_isolation ON {_ASSIGNMENTS}
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )
    op.execute(f"ALTER TABLE {_ROLE_DEMOTIONS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_ROLE_DEMOTIONS} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {_ROLE_DEMOTIONS}_tenant_isolation ON {_ROLE_DEMOTIONS}
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )


def downgrade() -> None:
    now = datetime.now(UTC)
    bind = op.get_bind()
    with force_rls_suspended(bind, "users", "refresh_tokens", _ROLE_DEMOTIONS):
        unmigrated_account_admins = list(
            bind.execute(
                sa.text(
                    f"""
                    SELECT users.id
                    FROM users
                    LEFT JOIN {_ROLE_DEMOTIONS}
                      ON {_ROLE_DEMOTIONS}.user_id = users.id
                     AND {_ROLE_DEMOTIONS}.organization_id = users.organization_id
                    WHERE users.role = 'account_admin'
                      AND {_ROLE_DEMOTIONS}.user_id IS NULL
                    ORDER BY users.id
                    """
                )
            ).scalars()
        )
        if unmigrated_account_admins:
            identities = ", ".join(str(user_id) for user_id in unmigrated_account_admins)
            raise RuntimeError(
                f"Cannot downgrade while post-migration account administrators exist: {identities}"
            )

        bind.execute(
            sa.text(
                f"""
                UPDATE refresh_tokens
                SET revoked_at = :now, revoked_reason = 'authorization_changed'
                WHERE revoked_at IS NULL
                  AND user_id IN (
                      SELECT {_ROLE_DEMOTIONS}.user_id
                      FROM {_ROLE_DEMOTIONS}
                      JOIN users ON users.id = {_ROLE_DEMOTIONS}.user_id
                      WHERE users.role = 'account_admin'
                  )
                """
            ),
            {"now": now},
        )
        bind.execute(
            sa.text(
                f"UPDATE users SET role = 'admin', "
                "authorization_version = authorization_version + 1 "
                f"WHERE role = 'account_admin' AND id IN (SELECT user_id FROM {_ROLE_DEMOTIONS})"
            )
        )

    op.execute(f"DROP POLICY IF EXISTS {_ROLE_DEMOTIONS}_tenant_isolation ON {_ROLE_DEMOTIONS}")
    op.execute(f"ALTER TABLE {_ROLE_DEMOTIONS} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_ROLE_DEMOTIONS} DISABLE ROW LEVEL SECURITY")
    op.drop_table(_ROLE_DEMOTIONS)
    op.execute(f"DROP POLICY IF EXISTS {_ASSIGNMENTS}_tenant_isolation ON {_ASSIGNMENTS}")
    op.execute(f"ALTER TABLE {_ASSIGNMENTS} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_ASSIGNMENTS} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_organization_owner_assignments_status", table_name=_ASSIGNMENTS)
    op.drop_table(_ASSIGNMENTS)
    with force_rls_suspended(bind, _BINDINGS):
        bind.execute(sa.text(f"DELETE FROM {_BINDINGS} WHERE role_bundle = 'org_owner'"))
    op.drop_index("uq_authorization_bindings_active_org_owner", table_name=_BINDINGS)
    op.drop_constraint("ck_authorization_bindings_role_bundle", _BINDINGS, type_="check")
    op.create_check_constraint(
        "ck_authorization_bindings_role_bundle", _BINDINGS, _FOUNDATION_BUNDLE_CHECK
    )
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", _LEGACY_USER_ROLE_CHECK)
