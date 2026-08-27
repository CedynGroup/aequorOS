"""Persistence boundary for scoped authorization and session invalidation.

No API route exposes these mutations yet.  The service is intentionally ready
for that later vertical slice: creating a binding validates tenant ownership and
atomically advances the target principal's authorization version while revoking
every refresh-token family.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    AuthorizationDecision,
    BindingGrant,
    BindingStatus,
    ConditionCheck,
    InstitutionScope,
    ModuleScope,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
    SensitivityScope,
    principal_bundle_compatible,
)
from app.core.authorization import (
    evaluate_permission as evaluate_grants,
)
from app.db.base import utc_now
from app.models import AuthorizationBinding, Bank, OperatorUser, User
from app.services import authentication


class AuthorizationInvariantError(ValueError):
    """A requested binding would violate the authorization authority model."""


class GrantorType(StrEnum):
    SYSTEM = "system"
    TENANT_USER = "tenant_user"
    OPERATOR = "operator"


@dataclass(frozen=True)
class GrantorRef:
    kind: GrantorType
    identifier: str


@dataclass(frozen=True)
class BindingScope:
    institution_scope: InstitutionScope
    institution_id: str | None
    module_scope: ModuleScope
    sensitivity_scope: SensitivityScope


def _principal_type(user: User) -> PrincipalType:
    return PrincipalType.MACHINE if user.auth_provider == "service" else PrincipalType.HUMAN


def _binding_grant(binding: AuthorizationBinding) -> BindingGrant:
    return BindingGrant(
        binding_id=binding.id,
        organization_id=binding.organization_id,
        principal_id=binding.principal_user_id,
        principal_type=PrincipalType(binding.principal_type),
        role_bundle=RoleBundle(binding.role_bundle),
        institution_scope=InstitutionScope(binding.institution_scope),
        institution_id=binding.institution_id,
        module_scope=ModuleScope(binding.module_scope),
        sensitivity_scope=SensitivityScope(binding.sensitivity_scope),
        status=BindingStatus(binding.status),
        valid_from=binding.valid_from,
        valid_until=binding.valid_until,
        revoked_at=binding.revoked_at,
    )


def _validate_scope(db: Session, organization_id: str, scope: BindingScope) -> None:
    if scope.institution_scope is InstitutionScope.ORGANIZATION:
        if scope.institution_id is not None:
            raise AuthorizationInvariantError(
                "organization-wide scope must not carry an institution id"
            )
        return
    if scope.institution_id is None:
        raise AuthorizationInvariantError("institution-specific scope requires an institution id")
    institution = db.scalar(
        select(Bank.id).where(
            Bank.id == scope.institution_id,
            Bank.organization_id == organization_id,
        )
    )
    if institution is None:
        raise AuthorizationInvariantError(
            "institution does not belong to the binding's organization"
        )


def _validate_grantor(db: Session, organization_id: str, grantor: GrantorRef) -> None:
    identifier = grantor.identifier.strip()
    if not identifier:
        raise AuthorizationInvariantError("grantor identifier must be non-empty")
    if grantor.kind is GrantorType.SYSTEM:
        return
    try:
        grantor_id = UUID(identifier)
    except ValueError as exc:
        raise AuthorizationInvariantError("user/operator grantor id must be a UUID") from exc
    if grantor.kind is GrantorType.TENANT_USER:
        user = db.scalar(
            select(User.id).where(
                User.id == grantor_id,
                User.organization_id == organization_id,
                User.is_active.is_(True),
            )
        )
        if user is None:
            raise AuthorizationInvariantError(
                "tenant-user grantor is not active in the binding's organization"
            )
        return
    operator = db.scalar(
        select(OperatorUser.id).where(
            OperatorUser.id == grantor_id,
            OperatorUser.is_active.is_(True),
        )
    )
    if operator is None:
        raise AuthorizationInvariantError("operator grantor is not active")


def invalidate_user_authorization(
    db: Session,
    *,
    organization_id: str,
    user_id: UUID,
    reason: str,
    commit: bool = True,
) -> int:
    """Atomically advance the authority version and end every refresh family.

    Future role, scope, status, and security mutations must call this in their
    transaction.  The user-row lock is the same serialization point used by
    refresh rotation, so a concurrent rotation cannot escape the revocation.
    """

    if not reason.strip():
        raise AuthorizationInvariantError("authorization changes require a reason")
    user = db.scalar(
        select(User)
        .where(User.id == user_id, User.organization_id == organization_id)
        .with_for_update(key_share=True)
    )
    if user is None:
        raise AuthorizationInvariantError("principal is not a member of the organization")
    user.authorization_version += 1
    authentication.revoke_user_refresh_tokens(
        db,
        user.id,
        reason="authorization_changed",
        commit=False,
    )
    db.flush()
    if commit:
        db.commit()
    return user.authorization_version


def create_role_binding(  # noqa: PLR0913 - every binding dimension is explicit
    db: Session,
    *,
    organization_id: str,
    principal_user_id: UUID,
    principal_type: PrincipalType,
    role_bundle: RoleBundle,
    scope: BindingScope,
    grantor: GrantorRef,
    reason: str,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> AuthorizationBinding:
    """Create one indivisible grant and invalidate existing sessions.

    This is a service primitive, not a tenant administration surface.  Future
    endpoints must add delegation and SoD policy before calling it.
    """

    grant_reason = reason.strip()
    if not grant_reason:
        raise AuthorizationInvariantError("a role binding requires a grant reason")
    principal = db.scalar(
        select(User).where(
            User.id == principal_user_id,
            User.organization_id == organization_id,
        )
    )
    if principal is None:
        raise AuthorizationInvariantError("principal is not a member of the organization")
    actual_type = _principal_type(principal)
    if principal_type is not actual_type:
        raise AuthorizationInvariantError("principal type does not match the identity record")
    if not principal_bundle_compatible(principal_type, role_bundle):
        if principal_type is PrincipalType.MACHINE:
            raise AuthorizationInvariantError(
                "machine principals require a machine permission bundle"
            )
        raise AuthorizationInvariantError("human principals cannot receive a machine bundle")
    _validate_scope(db, organization_id, scope)
    _validate_grantor(db, organization_id, grantor)

    starts_at = valid_from or utc_now()
    if valid_until is not None and valid_until <= starts_at:
        raise AuthorizationInvariantError("binding validity must end after it starts")
    binding = AuthorizationBinding(
        organization_id=organization_id,
        principal_user_id=principal_user_id,
        principal_type=principal_type.value,
        role_bundle=role_bundle.value,
        institution_scope=scope.institution_scope.value,
        institution_id=scope.institution_id,
        module_scope=scope.module_scope.value,
        sensitivity_scope=scope.sensitivity_scope.value,
        granted_by_type=grantor.kind.value,
        granted_by_id=grantor.identifier.strip(),
        grant_reason=grant_reason,
        granted_at=utc_now(),
        status=BindingStatus.ACTIVE.value,
        valid_from=starts_at,
        valid_until=valid_until,
    )
    db.add(binding)
    db.flush()
    invalidate_user_authorization(
        db,
        organization_id=organization_id,
        user_id=principal_user_id,
        reason=f"role binding granted: {grant_reason}",
        commit=False,
    )
    db.commit()
    db.refresh(binding)
    return binding


def evaluate_permission(  # noqa: PLR0913 - the complete decision tuple is explicit
    db: Session,
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
    *,
    conditions: tuple[ConditionCheck, ...] = (),
    now: datetime | None = None,
) -> AuthorizationDecision:
    """Resolve authority from persisted bindings only, with an audit-ready trace."""

    bindings = list(
        db.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.organization_id == principal.organization_id,
                AuthorizationBinding.principal_user_id == principal.principal_id,
                AuthorizationBinding.principal_type == principal.principal_type.value,
            )
        )
    )
    decision = evaluate_grants(
        principal,
        permission,
        resource,
        [_binding_grant(binding) for binding in bindings],
        conditions=conditions,
        now=now,
    )
    user = db.scalar(
        select(User).where(
            User.id == principal.principal_id,
            User.organization_id == principal.organization_id,
            User.is_active.is_(True),
        )
    )
    if user is None or _principal_type(user) is not principal.principal_type:
        return replace(decision, allowed=False, reason="principal_not_active")
    if resource.institution_id is not None:
        institution = db.scalar(
            select(Bank.id).where(
                Bank.id == resource.institution_id,
                Bank.organization_id == resource.organization_id,
            )
        )
        if institution is None:
            return replace(decision, allowed=False, reason="resource_institution_not_in_tenant")
    return decision
