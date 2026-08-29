"""Org Owner administration of indivisible scoped role bindings."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import DbSession, GrantAdminTenant, TenantContext
from app.core.authorization import (
    ROLE_PERMISSIONS,
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    RoleBundle,
    SensitivityScope,
)
from app.models import AuthorizationBinding, Bank, Organization, User
from app.schemas.authorization import (
    BindingCreateRequest,
    BindingCreateResponse,
    BindingListRead,
    BindingPreviewRead,
    BindingPreviewRequest,
    BindingRead,
    BindingRevokeRequest,
    MemberListRead,
    MemberRead,
    ScopedGrantInput,
    SodDecisionRead,
    SodPolicyFindingRead,
)
from app.services import authorization, grant_administration

router = APIRouter(tags=["authorization"])

_LEGACY_REVOKER_ID = "revoker-not-recorded-predates-attribution"
_LEGACY_REVOKER_NAME = "Revoker not recorded (predates attribution requirement)"


def _sod_read(decision: grant_administration.SodDecision) -> SodDecisionRead:
    return SodDecisionRead(
        outcome=decision.outcome.value,
        findings=[
            SodPolicyFindingRead(code=finding.code, message=finding.message)
            for finding in decision.findings
        ],
    )


def grant_conflict(exc: grant_administration.GrantAdministrationError) -> HTTPException:
    details: dict[str, object] = {
        "error_code": "scoped_grant_refused",
        "message": str(exc),
    }
    if isinstance(exc, grant_administration.SodPolicyBlocked):
        details["sod_decision"] = _sod_read(exc.decision).model_dump(mode="json")
    if isinstance(exc, grant_administration.DuplicateScopedGrant):
        details["existing_binding_id"] = str(exc.binding_id)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=details)


def _display_name(user: User | None, fallback: str) -> str:
    if user is None:
        return fallback
    return user.display_name or user.email


def _actor_name(
    actor_type: str | None,
    actor_id: str | None,
    users: dict[UUID, User],
) -> str | None:
    if actor_type is None or actor_id is None:
        return None
    if actor_type == GrantorType.SYSTEM.value and actor_id == _LEGACY_REVOKER_ID:
        return _LEGACY_REVOKER_NAME
    if actor_type == GrantorType.SYSTEM.value:
        return "AequorOS system"
    if actor_type == GrantorType.OPERATOR.value:
        return "AequorOS operator"
    try:
        user_id = UUID(actor_id)
    except ValueError:
        return actor_id
    return _display_name(users.get(user_id), actor_id)


def _binding_read(
    binding: AuthorizationBinding,
    *,
    users: dict[UUID, User],
    banks: dict[str, Bank],
    organization: Organization,
    authority_sentence_override: str | None = None,
) -> BindingRead:
    principal = users.get(binding.principal_user_id)
    role_bundle = RoleBundle(binding.role_bundle)
    effective = bool(
        principal and principal.is_active
    ) and grant_administration.binding_is_effective(binding)
    return BindingRead(
        id=binding.id,
        principal_user_id=binding.principal_user_id,
        principal_name=_display_name(principal, str(binding.principal_user_id)),
        role_bundle=binding.role_bundle,
        institution_scope=InstitutionScope(binding.institution_scope),
        institution_id=binding.institution_id,
        institution_name=(
            banks[binding.institution_id].name
            if binding.institution_id is not None and binding.institution_id in banks
            else None
        ),
        module_scope=ModuleScope(binding.module_scope),
        sensitivity_scope=SensitivityScope(binding.sensitivity_scope),
        status=BindingStatus(binding.status),
        effective=effective,
        authority_sentence=(
            authority_sentence_override
            or grant_administration.compose_authority_sentence(
                principal_name=_display_name(principal, str(binding.principal_user_id)),
                role_bundle=role_bundle,
                institution_name=(
                    f"every institution in {organization.name}"
                    if binding.institution_scope == InstitutionScope.ORGANIZATION.value
                    else banks[binding.institution_id].name
                    if binding.institution_id is not None and binding.institution_id in banks
                    else str(binding.institution_id)
                ),
                module_scope=ModuleScope(binding.module_scope),
                sensitivity_scope=SensitivityScope(binding.sensitivity_scope),
            )
        ),
        effective_permissions=sorted(
            permission.value for permission in ROLE_PERMISSIONS[role_bundle]
        ),
        granted_by_type=GrantorType(binding.granted_by_type),
        granted_by_id=binding.granted_by_id,
        granted_by_name=_actor_name(binding.granted_by_type, binding.granted_by_id, users)
        or binding.granted_by_id,
        grant_reason=binding.grant_reason,
        granted_at=binding.granted_at,
        valid_from=binding.valid_from,
        valid_until=binding.valid_until,
        revoked_at=binding.revoked_at,
        revoked_by_type=(
            GrantorType(binding.revoked_by_type) if binding.revoked_by_type is not None else None
        ),
        revoked_by_id=binding.revoked_by_id,
        revoked_by_name=_actor_name(binding.revoked_by_type, binding.revoked_by_id, users),
        revoked_reason=binding.revoked_reason,
    )


def _presentation_maps(
    db: DbSession, organization_id: str
) -> tuple[dict[UUID, User], dict[str, Bank], Organization]:
    users = {
        user.id: user
        for user in db.scalars(select(User).where(User.organization_id == organization_id))
    }
    banks = {
        bank.id: bank
        for bank in db.scalars(select(Bank).where(Bank.organization_id == organization_id))
    }
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    return users, banks, organization


def binding_response(
    db: DbSession,
    organization_id: str,
    result: grant_administration.GrantResult,
) -> BindingCreateResponse:
    users, banks, organization = _presentation_maps(db, organization_id)
    return BindingCreateResponse(
        binding=_binding_read(
            result.binding,
            users=users,
            banks=banks,
            organization=organization,
            authority_sentence_override=result.authority_sentence,
        ),
        sod_decision=_sod_read(result.sod_decision),
    )


def binding_scope(payload: ScopedGrantInput) -> authorization.BindingScope:
    return authorization.BindingScope(
        institution_scope=payload.institution_scope,
        institution_id=payload.institution_id,
        module_scope=payload.module_scope,
        sensitivity_scope=payload.sensitivity_scope,
    )


def _member_or_404(db: DbSession, ctx: TenantContext, user_id: UUID) -> User:
    member = db.scalar(
        select(User).where(User.id == user_id, User.organization_id == ctx.organization_id)
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found.")
    return member


@router.get(
    "/authorization/bindings",
    response_model=BindingListRead,
    operation_id="listAuthorizationBindings",
)
def list_authorization_bindings(
    db: DbSession,
    ctx: GrantAdminTenant,
    principal_user_id: Annotated[UUID | None, Query()] = None,
) -> BindingListRead:
    statement = select(AuthorizationBinding).where(
        AuthorizationBinding.organization_id == ctx.organization_id
    )
    if principal_user_id is not None:
        _member_or_404(db, ctx, principal_user_id)
        statement = statement.where(AuthorizationBinding.principal_user_id == principal_user_id)
    rows = list(
        db.scalars(
            statement.order_by(
                AuthorizationBinding.granted_at.desc(),
                AuthorizationBinding.id.desc(),
            )
        )
    )
    users, banks, organization = _presentation_maps(db, ctx.organization_id)
    return BindingListRead(
        bindings=[
            _binding_read(row, users=users, banks=banks, organization=organization) for row in rows
        ]
    )


@router.post(
    "/authorization/bindings/preview",
    response_model=BindingPreviewRead,
    operation_id="previewAuthorizationBinding",
)
def preview_authorization_binding(
    payload: BindingPreviewRequest,
    db: DbSession,
    ctx: GrantAdminTenant,
) -> BindingPreviewRead:
    _member_or_404(db, ctx, payload.principal_user_id)
    scope = binding_scope(payload)
    try:
        grant_administration.validate_public_grant(RoleBundle(payload.role_bundle), scope)
        sentence = grant_administration.scoped_authority_sentence(
            db,
            organization_id=ctx.organization_id,
            principal_user_id=payload.principal_user_id,
            role_bundle=RoleBundle(payload.role_bundle),
            scope=scope,
        )
    except grant_administration.GrantAdministrationError as exc:
        raise grant_conflict(exc) from exc
    return BindingPreviewRead(authority_sentence=sentence)


@router.post(
    "/authorization/bindings",
    response_model=BindingCreateResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAuthorizationBinding",
)
def create_authorization_binding(
    payload: BindingCreateRequest,
    db: DbSession,
    ctx: GrantAdminTenant,
) -> BindingCreateResponse:
    _member_or_404(db, ctx, payload.principal_user_id)
    assert ctx.actor_user_id is not None  # guaranteed by GrantAdminTenant
    try:
        result = grant_administration.create_scoped_grant(
            db,
            organization_id=ctx.organization_id,
            principal_user_id=payload.principal_user_id,
            role_bundle=RoleBundle(payload.role_bundle),
            scope=binding_scope(payload),
            actor_user_id=ctx.actor_user_id,
            reason=payload.reason,
            expected_authority_sentence=payload.expected_authority_sentence,
        )
    except (
        grant_administration.GrantAdministrationError,
        authorization.AuthorizationInvariantError,
    ) as exc:
        if isinstance(exc, authorization.AuthorizationInvariantError):
            exc = grant_administration.GrantAdministrationError(str(exc))
        raise grant_conflict(exc) from exc
    return binding_response(db, ctx.organization_id, result)


@router.post(
    "/authorization/bindings/{binding_id}/revoke",
    response_model=BindingRead,
    operation_id="revokeAuthorizationBinding",
)
def revoke_authorization_binding(
    binding_id: UUID,
    payload: BindingRevokeRequest,
    db: DbSession,
    ctx: GrantAdminTenant,
) -> BindingRead:
    assert ctx.actor_user_id is not None  # guaranteed by GrantAdminTenant
    try:
        binding = grant_administration.revoke_scoped_grant(
            db,
            organization_id=ctx.organization_id,
            binding_id=binding_id,
            actor_user_id=ctx.actor_user_id,
            reason=payload.reason,
        )
    except grant_administration.GrantAdministrationError as exc:
        raise grant_conflict(exc) from exc
    users, banks, organization = _presentation_maps(db, ctx.organization_id)
    return _binding_read(binding, users=users, banks=banks, organization=organization)


def _lifecycle_status(user: User) -> Literal["active", "invited", "deactivated"]:
    if user.is_active:
        return "active"
    if (
        user.auth_provider == "password"
        and user.password_hash is None
        and user.last_login_at is None
    ):
        return "invited"
    return "deactivated"


def _access_request_state(user: User) -> Literal["none", "approval_needed", "rejected"]:
    if user.access_rejected_at is not None:
        return "rejected"
    if (
        not user.is_active
        and user.auth_provider == "oidc"
        and user.password_hash is None
        and user.last_login_at is None
    ):
        return "approval_needed"
    return "none"


def _authentication_method(user: User) -> Literal["password", "sso", "service"]:
    if user.auth_provider == "oidc":
        return "sso"
    if user.auth_provider == "service":
        return "service"
    return "password"


@router.get(
    "/organization/members",
    response_model=MemberListRead,
    operation_id="listOrganizationMembers",
)
def list_organization_members(db: DbSession, ctx: GrantAdminTenant) -> MemberListRead:
    users, banks, organization = _presentation_maps(db, ctx.organization_id)
    bindings = list(
        db.scalars(
            select(AuthorizationBinding)
            .where(AuthorizationBinding.organization_id == ctx.organization_id)
            .order_by(
                AuthorizationBinding.granted_at.desc(),
                AuthorizationBinding.id.desc(),
            )
        )
    )
    by_principal: dict[UUID, list[AuthorizationBinding]] = defaultdict(list)
    for binding in bindings:
        by_principal[binding.principal_user_id].append(binding)

    members: list[MemberRead] = []
    for user in sorted(
        users.values(), key=lambda row: ((row.display_name or row.email).lower(), row.email)
    ):
        grant_reads = [
            _binding_read(binding, users=users, banks=banks, organization=organization)
            for binding in by_principal[user.id]
        ]
        members.append(
            MemberRead(
                user_id=user.id,
                email=user.email,
                display_name=user.display_name,
                job_title=user.job_title,
                lifecycle_status=_lifecycle_status(user),
                access_request_state=_access_request_state(user),
                last_activity_at=user.last_login_at,
                authentication_method=_authentication_method(user),
                active_grant_count=sum(grant.effective for grant in grant_reads),
                grants=grant_reads,
            )
        )
    return MemberListRead(members=members)
