"""Lifecycle for the explicit baseline tenant-membership binding."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.models import AuthorizationBinding, User
from app.services import authorization

MEMBERSHIP_GRANT_REASON = (
    "Baseline membership: allow an active organization member to load the console shell "
    "and use personal self-service without granting institution or product-module authority"
)
MEMBERSHIP_REVOKE_REASON = "Baseline membership ended because the member was deactivated"


def _active_membership(
    db: Session,
    *,
    organization_id: str,
    user_id: UUID,
) -> AuthorizationBinding | None:
    return db.scalar(
        select(AuthorizationBinding).where(
            AuthorizationBinding.organization_id == organization_id,
            AuthorizationBinding.principal_user_id == user_id,
            AuthorizationBinding.principal_type == PrincipalType.HUMAN.value,
            AuthorizationBinding.role_bundle == RoleBundle.MEMBER.value,
            AuthorizationBinding.status == BindingStatus.ACTIVE.value,
        )
    )


def ensure_baseline_membership(
    db: Session,
    *,
    user: User,
    granted_by_id: str,
    commit: bool = True,
) -> AuthorizationBinding:
    """Create the non-permission baseline exactly when a human becomes active."""

    if not user.is_active or user.auth_provider == "service":
        raise authorization.AuthorizationInvariantError(
            "baseline membership requires an active human organization member"
        )
    existing = _active_membership(
        db,
        organization_id=user.organization_id,
        user_id=user.id,
    )
    if existing is not None:
        if (
            existing.institution_scope != InstitutionScope.ORGANIZATION.value
            or existing.institution_id is not None
            or existing.module_scope != ModuleScope.ACCOUNT.value
            or existing.sensitivity_scope != SensitivityScope.RESTRICTED.value
            or existing.valid_until is not None
            or existing.granted_by_type != GrantorType.SYSTEM.value
        ):
            raise authorization.AuthorizationInvariantError(
                "existing baseline membership does not have the canonical scope and lifecycle"
            )
        return existing

    binding = authorization.create_role_binding(
        db,
        organization_id=user.organization_id,
        principal_user_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.MEMBER,
        scope=authorization.BindingScope(
            institution_scope=InstitutionScope.ORGANIZATION,
            institution_id=None,
            module_scope=ModuleScope.ACCOUNT,
            sensitivity_scope=SensitivityScope.RESTRICTED,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, granted_by_id),
        reason=MEMBERSHIP_GRANT_REASON,
        commit=False,
    )
    authorization.record_binding_grant_audit(
        db,
        binding=binding,
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, granted_by_id),
        authority_sentence=(
            f"{user.display_name or user.email} is an active organization member "
            "with console and personal self-service access only."
        ),
    )
    if commit:
        db.commit()
        db.refresh(binding)
    return binding


def end_baseline_membership(
    db: Session,
    *,
    user: User,
    commit: bool = True,
) -> AuthorizationBinding | None:
    """Revoke the baseline when, and only when, the member is deactivated."""

    binding = _active_membership(
        db,
        organization_id=user.organization_id,
        user_id=user.id,
    )
    if binding is None:
        return None
    moment = utc_now()
    binding.status = BindingStatus.REVOKED.value
    binding.revoked_at = moment
    binding.revoked_by_type = GrantorType.SYSTEM.value
    binding.revoked_by_id = "user_deactivation"
    binding.revoked_reason = MEMBERSHIP_REVOKE_REASON
    authorization.record_binding_revoke_audit(
        db,
        binding=binding,
        actor_user_id=None,
        authority_sentence=(
            f"{user.display_name or user.email} no longer has baseline membership."
        ),
    )
    if commit:
        db.commit()
        db.refresh(binding)
    return binding
