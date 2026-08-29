"""Assign the first organization owner when a new tenant is provisioned.

The one-time backfill migration does the same work in raw SQL because Alembic
revisions cannot import application code that may change over time.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
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
