"""Database operations for authorization bindings and session invalidation.

No API route exposes these operations yet. The service is ready for a future
admin endpoint: creating a binding checks tenant ownership and updates the
user's authorization version in the same transaction, which also revokes all
their refresh tokens.
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
from app.core.observability import authorization_shadow_decision
from app.db.base import utc_now
from app.models import AuthorizationBinding, Bank, OperatorUser, User
from app.services import authentication


class AuthorizationInvariantError(ValueError):
    """A requested binding would break the authorization rules."""


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
    """Update the user's authorization version and revoke all their refresh tokens.

    Any change to a user's role, scope, status, or security settings must call
    this in the same transaction. The user-row lock is shared with refresh
    token rotation, so a concurrent token refresh cannot skip the revocation.
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
    commit: bool = True,
) -> AuthorizationBinding:
    """Create a single binding and invalidate the user's existing sessions.

    This is a low-level service function, not a tenant admin API. Future
    endpoints must add delegation and segregation-of-duties checks before
    calling it.
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
    if commit:
        db.commit()
        db.refresh(binding)
    else:
        db.flush()
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
    """Check permissions using only stored bindings, returning a trace for audit."""

    user = db.scalar(
        select(User).where(
            User.id == principal.principal_id,
            User.organization_id == principal.organization_id,
            User.is_active.is_(True),
        )
    )
    if user is None or _principal_type(user) is not principal.principal_type:
        decision = evaluate_grants(
            principal,
            permission,
            resource,
            (),
            conditions=conditions,
            now=now,
        )
        return replace(decision, allowed=False, reason="principal_not_active")
    if resource.organization_id != principal.organization_id:
        return evaluate_grants(
            principal,
            permission,
            resource,
            (),
            conditions=conditions,
            now=now,
        )
    if resource.institution_scope is InstitutionScope.INSTITUTION:
        institution = db.scalar(
            select(Bank.id).where(
                Bank.id == resource.institution_id,
                Bank.organization_id == resource.organization_id,
            )
        )
        if institution is None:
            decision = evaluate_grants(
                principal,
                permission,
                resource,
                (),
                conditions=conditions,
                now=now,
            )
            return replace(decision, allowed=False, reason="resource_institution_not_in_tenant")
    bindings = list(
        db.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.organization_id == principal.organization_id,
                AuthorizationBinding.principal_user_id == principal.principal_id,
                AuthorizationBinding.principal_type == principal.principal_type.value,
            )
        )
    )
    return evaluate_grants(
        principal,
        permission,
        resource,
        [_binding_grant(binding) for binding in bindings],
        conditions=conditions,
        now=now,
    )


def observe_shadow_permission(  # noqa: PLR0913 - the observed decision tuple is explicit
    db: Session,
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
    *,
    legacy_allowed: bool,
    conditions: tuple[ConditionCheck, ...] = (),
    now: datetime | None = None,
) -> AuthorizationDecision | None:
    """Evaluate the binding result and log it for comparison with the legacy check.

    This function never allows or denies the request itself. It gives a real
    route a way to log what the new binding evaluator would say, so the two
    can be compared during the rollout without switching the route over.
    """

    target_fields = {
        "organization_id": resource.organization_id,
        "principal_id": str(principal.principal_id),
        "principal_type": principal.principal_type.value,
        "permission": permission.value,
        "institution_scope": resource.institution_scope.value,
        "institution_id": resource.institution_id,
        "module": resource.module.value,
        "sensitivity": resource.sensitivity.value,
    }
    try:
        with db.begin_nested():
            decision = evaluate_permission(
                db,
                principal,
                permission,
                resource,
                conditions=conditions,
                now=now,
            )
    except Exception as exc:  # noqa: BLE001 - shadow observation must never become a route gate
        authorization_shadow_decision(
            binding_allowed=False,
            legacy_allowed=legacy_allowed,
            reason="shadow_evaluation_failed",
            severity="error",
            error_type=type(exc).__name__,
            **target_fields,
        )
        return None
    authorization_shadow_decision(
        binding_allowed=decision.allowed,
        legacy_allowed=legacy_allowed,
        reason=decision.reason,
        matching_binding_ids=",".join(str(value) for value in decision.matching_binding_ids),
        binding_trace=",".join(
            f"{trace.binding_id}:{trace.reason}" for trace in decision.binding_trace
        ),
        **target_fields,
    )
    return decision
