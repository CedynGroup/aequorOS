"""Shared authorization-fixture operations for the authorization integration suites.

Suspensions route through the production invalidation entry point
(``authorization.invalidate_user_authorization``) so a fixture state change
exercises the same user-row lock, refresh-token revocation and token-rotation
coordination as production, and fixture user lookups are tenant-scoped by
construction.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import BindingStatus
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import AuthorizationBinding, User


def open_tenant_session(organization_id: str) -> Session:
    """Open a session whose row-level-scoping GUC targets the fixture tenant."""
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    return session


def tenant_user(db: Session, *, organization_id: str, user_id: UUID) -> User:
    """Fetch one principal inside the fixture tenant, never across tenants."""
    user = db.scalar(
        select(User).where(User.id == user_id, User.organization_id == organization_id)
    )
    assert user is not None
    return user


def current_authorization_version(db: Session, *, organization_id: str, user_id: UUID) -> int:
    """Read the tenant-scoped principal's current authorization version."""
    return tenant_user(db, organization_id=organization_id, user_id=user_id).authorization_version


def suspend_all_bindings(db: Session, *, organization_id: str, user_id: UUID, reason: str) -> int:
    """Suspend every active binding of one principal and invalidate their authorization.

    The binding statuses flip in the caller's transaction; the version bump,
    refresh-token revocation and token-rotation coordination happen inside the
    production helper under the user-row lock, which commits. Returns the new
    authorization version.
    """
    bindings = list(
        db.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.principal_user_id == user_id,
                AuthorizationBinding.organization_id == organization_id,
                AuthorizationBinding.status == BindingStatus.ACTIVE.value,
            )
        )
    )
    for binding in bindings:
        binding.status = BindingStatus.SUSPENDED.value
    return authorization.invalidate_user_authorization(
        db, organization_id=organization_id, user_id=user_id, reason=reason
    )
