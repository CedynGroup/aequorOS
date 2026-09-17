"""Assign the first organization owner when a new tenant is provisioned.

Ownership is written as TWO explicit sentences, never one implied one:

* ``org_owner`` / organization-wide / Account / all — the account-plane
  authority: members, grants, SSO, keys. Its bundle carries ``administer``
  only, so on its own it shows no product module at all.
* ``viewer`` / organization-wide / all modules / all sensitivities — the read
  access docs/rbac.md §7 gives an Owner ("see dashboards for administration
  context but hold no operational write"). It is a separate row so it is
  visible in Members, revocable on its own, and never inferred from the owner
  bundle. Maker/checker authority stays out by SoD C9.

On 2026-09-16 an Owner holding only the first sentence signed in to production
and met a 404: the account plane was theirs, the product was not.

The one-time backfill migrations do the same work in raw SQL because Alembic
revisions cannot import application code that may change over time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
    InstitutionScope,
    ModuleScope,
    OwnerAssignmentBasis,
    OwnerAssignmentStatus,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.security import ACCOUNT_ADMIN_ROLE, ADMIN_ROLE
from app.models import AuthorizationBinding, OrganizationOwnerAssignment, User
from app.services import authorization

ELIGIBLE_ADMIN_ROLES = frozenset({ADMIN_ROLE, ACCOUNT_ADMIN_ROLE})
AUTO_ASSIGNMENT_REASON = (
    "Initial Org Owner auto-assignment: exactly one eligible active human administrator existed"
)
OWNER_READ_REASON = (
    "Org Owner read access: the account owner sees every module read-only for "
    "administration context (docs/rbac.md §7 permission matrix); operational "
    "maker/checker authority stays separate (SoD C9)"
)
# Any of these bundles carries `view`; an organization-wide all/all row of one of
# them already gives the Owner the read access the second sentence would add.
OWNER_READ_BUNDLES = frozenset(
    {RoleBundle.VIEWER, RoleBundle.AUDITOR, RoleBundle.ANALYST, RoleBundle.APPROVER}
)


class OwnerAssignmentError(ValueError):
    """The requested owner assignment breaks the assignment rules."""


def eligible_admin_candidates(db: Session, organization_id: str) -> list[User]:
    """Return active human legacy/account administrators in stable order."""

    return list(
        db.scalars(
            select(User)
            .where(
                User.organization_id == organization_id,
                User.role.in_(ELIGIBLE_ADMIN_ROLES),
                User.is_active.is_(True),
                User.auth_provider != "service",
            )
            .order_by(User.email, User.id)
        )
    )


def candidate_snapshot(user: User) -> dict[str, str | None]:
    """A snapshot of the user's ID, email, and name for staff to review."""

    return {
        "user_id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
    }


def assign_initial_owner(  # noqa: PLR0913 - provenance is intentionally explicit
    db: Session,
    *,
    organization_id: str,
    candidate: User,
    granted_by_id: str,
    commit: bool = True,
) -> OrganizationOwnerAssignment:
    """Assign the single eligible administrator as owner and record why.

    This function does not pick between candidates. The caller must first
    confirm there is exactly one eligible administrator. The function
    re-checks the eligibility query so a stale or hand-picked candidate
    cannot bypass the rule.
    """

    candidates = eligible_admin_candidates(db, organization_id)
    if len(candidates) != 1 or candidates[0].id != candidate.id:
        raise OwnerAssignmentError(
            "initial ownership requires exactly one eligible active human administrator"
        )
    if db.get(OrganizationOwnerAssignment, organization_id) is not None:
        raise OwnerAssignmentError("initial owner assignment state already exists")
    existing_owner = db.scalar(
        select(AuthorizationBinding.id).where(
            AuthorizationBinding.organization_id == organization_id,
            AuthorizationBinding.role_bundle == RoleBundle.ORG_OWNER.value,
        )
    )
    if existing_owner is not None:
        raise OwnerAssignmentError("organization already has an owner binding")

    binding = authorization.create_role_binding(
        db,
        organization_id=organization_id,
        principal_user_id=candidate.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ORG_OWNER,
        scope=authorization.BindingScope(
            institution_scope=InstitutionScope.ORGANIZATION,
            institution_id=None,
            module_scope=ModuleScope.ACCOUNT,
            sensitivity_scope=SensitivityScope.ALL,
        ),
        grantor=authorization.GrantorRef(
            authorization.GrantorType.SYSTEM,
            granted_by_id,
        ),
        reason=AUTO_ASSIGNMENT_REASON,
        commit=False,
    )
    ensure_owner_read_access(
        db,
        organization_id=organization_id,
        owner=candidate,
        granted_by_id=granted_by_id,
        commit=False,
    )
    state = OrganizationOwnerAssignment(
        organization_id=organization_id,
        status=OwnerAssignmentStatus.ASSIGNED.value,
        basis=OwnerAssignmentBasis.EXACTLY_ONE_ELIGIBLE_ADMIN.value,
        eligible_candidate_count=1,
        eligible_candidates=[candidate_snapshot(candidate)],
        owner_user_id=candidate.id,
        owner_binding_id=binding.id,
    )
    db.add(state)
    db.flush()
    if commit:
        db.commit()
        db.refresh(state)
    return state


def owner_read_access_exists(
    db: Session,
    *,
    organization_id: str,
    user_id,
    now: datetime | None = None,
) -> bool:
    """True when the user already holds an effective organization-wide read row.

    Only an exact ``all`` modules / ``all`` sensitivities organization-wide row
    of a view-carrying bundle counts. A narrower row (one module, one tier, one
    institution) is real authority but not the Owner's read sentence, and the
    evaluator unions rows, so adding the sentence beside it is still exact.
    """

    from app.services.grant_administration import (  # noqa: PLC0415 - avoid a service cycle
        binding_is_effective,
    )

    rows = db.scalars(
        select(AuthorizationBinding).where(
            AuthorizationBinding.organization_id == organization_id,
            AuthorizationBinding.principal_user_id == user_id,
            AuthorizationBinding.principal_type == PrincipalType.HUMAN.value,
            AuthorizationBinding.role_bundle.in_([bundle.value for bundle in OWNER_READ_BUNDLES]),
            AuthorizationBinding.institution_scope == InstitutionScope.ORGANIZATION.value,
            AuthorizationBinding.institution_id.is_(None),
            AuthorizationBinding.module_scope == ModuleScope.ALL.value,
            AuthorizationBinding.sensitivity_scope == SensitivityScope.ALL.value,
            AuthorizationBinding.status == BindingStatus.ACTIVE.value,
        )
    )
    return any(binding_is_effective(row, now=now) for row in rows)


def ensure_owner_read_access(
    db: Session,
    *,
    organization_id: str,
    owner: User,
    granted_by_id: str,
    commit: bool = True,
) -> AuthorizationBinding | None:
    """Write the Owner's read sentence unless an equivalent row already exists.

    Returns the new binding, or ``None`` when nothing was needed. Like every
    binding creation this advances the owner's ``authorization_version`` and
    revokes their refresh families, so a live owner signs in again.
    """

    if owner_read_access_exists(db, organization_id=organization_id, user_id=owner.id):
        return None
    return authorization.create_role_binding(
        db,
        organization_id=organization_id,
        principal_user_id=owner.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            institution_scope=InstitutionScope.ORGANIZATION,
            institution_id=None,
            module_scope=ModuleScope.ALL,
            sensitivity_scope=SensitivityScope.ALL,
        ),
        grantor=authorization.GrantorRef(
            authorization.GrantorType.SYSTEM,
            granted_by_id,
        ),
        reason=OWNER_READ_REASON,
        commit=commit,
    )
