"""Org Owner administration of indivisible scoped role bindings."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, GrantAdminTenant, Tenant, TenantContext
from app.core.authorization import (
    ROLE_PERMISSIONS,
    BindingStatus,
    GrantorType,
    GrantReasonCategory,
    InstitutionScope,
    ModuleScope,
    Permission,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.models import (
    AuditEvent,
    AuthorizationAccessRequest,
    AuthorizationBinding,
    Bank,
    Organization,
    User,
)
from app.schemas.authorization import (
    AccessRequestApprove,
    AccessRequestCreate,
    AccessRequestListRead,
    AccessRequestRead,
    BindingCreateRequest,
    BindingCreateResponse,
    BindingListRead,
    BindingPreviewRead,
    BindingPreviewRequest,
    BindingRead,
    BindingRevokeRequest,
    InstitutionDirectoryEntryRead,
    InstitutionDirectoryRead,
    MemberListRead,
    MemberRead,
    ScopedGrantInput,
    SodDecisionRead,
    SodPolicyFindingRead,
)
from app.services import authorization, grant_administration

router = APIRouter(tags=["authorization"])

_PUBLIC_ACCESS_REQUEST_ROUTES = frozenset(
    {
        "/alerts",
        "/basel",
        "/basel/exposures",
        "/basel/loan-book",
        "/basel/planning",
        "/basel/rwa",
        "/basel/stress",
        "/basel/structure",
        "/behavioral",
        "/behavioral/deposit-stability",
        "/behavioral/liquidity",
        "/behavioral/nmd-duration",
        "/behavioral/prepayment",
        "/credit",
        "/credit/activity",
        "/credit/book",
        "/credit/concentration",
        "/credit/delinquency",
        "/credit/vintages",
        "/data-engine",
        "/data-engine/adapters",
        "/data-engine/api",
        "/data-engine/database",
        "/data-engine/excel-csv",
        "/data-engine/market-data",
        "/data-engine/positions",
        "/data-engine/t24",
        "/forecasting",
        "/forecasting/assumptions",
        "/forecasting/nii",
        "/forecasting/optimizer",
        "/forecasting/reverse-stress",
        "/forecasting/scenario",
        "/forecasting/whatif",
        "/ftp",
        "/ftp/expost",
        "/ftp/lines",
        "/ftp/products",
        "/ftp/rules",
        "/ftp/scenarios",
        "/fx",
        "/fx/forwards",
        "/fx/hedges",
        "/fx/limits",
        "/fx/scenarios",
        "/fx/var",
        "/icaap",
        "/institution",
        "/institution/history",
        "/institution/outlets",
        "/institution/parties",
        "/institution/products",
        "/institution/registers",
        "/irr",
        "/irr/gaps",
        "/irr/limits",
        "/irr/scenarios",
        "/irr/sensitivity",
        "/irr/standardised",
        "/liquidity",
        "/liquidity/buffer",
        "/liquidity/cfp",
        "/liquidity/forecast",
        "/liquidity/monitoring",
        "/liquidity/nsfr",
        "/liquidity/stress",
        "/markets",
        "/positions",
        "/reports",
        "/reports/analyses",
        "/reports/board-pack",
        "/reports/stress-board-pack",
        "/risk",
        "/submissions",
        "/submissions/approvals",
        "/submissions/calendar",
        "/submissions/compare",
        "/submissions/history",
        "/submissions/returns",
        "/submissions/settings",
        "/submissions/signatures",
        "/submissions/templates",
    }
)


def _route_requirement(
    route: str,
) -> tuple[str, tuple[tuple[ModuleScope, Sensitivity, Permission], ...]]:
    normalized = "/" + route.strip("/").lower()
    if normalized not in _PUBLIC_ACCESS_REQUEST_ROUTES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")

    if normalized == "/liquidity/stress":
        requirements = (
            (ModuleScope.LIQUIDITY, Sensitivity.CONFIDENTIAL, Permission.VIEW),
            (ModuleScope.RISK, Sensitivity.CONFIDENTIAL, Permission.VIEW),
        )
    elif normalized in {
        "/liquidity/forecast",
        "/liquidity/monitoring",
        "/liquidity/cfp",
    }:
        requirements = ((ModuleScope.LIQUIDITY, Sensitivity.CONFIDENTIAL, Permission.VIEW),)
    elif normalized == "/liquidity":
        requirements = (
            (ModuleScope.LIQUIDITY, Sensitivity.AGGREGATED, Permission.VIEW),
            (ModuleScope.LIQUIDITY, Sensitivity.CONFIDENTIAL, Permission.VIEW),
        )
    elif normalized.startswith("/liquidity"):
        requirements = ((ModuleScope.LIQUIDITY, Sensitivity.AGGREGATED, Permission.VIEW),)
    elif normalized == "/irr/scenarios":
        requirements = ((ModuleScope.IRRBB, Sensitivity.CONFIDENTIAL, Permission.VIEW),)
    elif normalized.startswith("/irr"):
        requirements = ((ModuleScope.IRRBB, Sensitivity.AGGREGATED, Permission.VIEW),)
    elif normalized == "/fx/scenarios":
        requirements = ((ModuleScope.FX, Sensitivity.CONFIDENTIAL, Permission.VIEW),)
    elif normalized.startswith("/fx"):
        requirements = ((ModuleScope.FX, Sensitivity.AGGREGATED, Permission.VIEW),)
    elif normalized == "/ftp/scenarios":
        requirements = ((ModuleScope.FTP, Sensitivity.CONFIDENTIAL, Permission.VIEW),)
    elif normalized in {"/basel/planning", "/icaap"}:
        requirements = ((ModuleScope.CAPITAL, Sensitivity.CONFIDENTIAL, Permission.VIEW),)
    else:
        root = normalized.split("/", 2)[1]
        requirement_by_root = {
            "alerts": (ModuleScope.RISK, Sensitivity.CONFIDENTIAL, Permission.VIEW),
            "basel": (ModuleScope.CAPITAL, Sensitivity.AGGREGATED, Permission.VIEW),
            "behavioral": (ModuleScope.BEHAVIORAL, Sensitivity.AGGREGATED, Permission.VIEW),
            "credit": (ModuleScope.RISK, Sensitivity.CONFIDENTIAL, Permission.VIEW),
            "data-engine": (ModuleScope.DATA, Sensitivity.RESTRICTED, Permission.VIEW),
            "forecasting": (ModuleScope.FORECASTING, Sensitivity.AGGREGATED, Permission.VIEW),
            "ftp": (ModuleScope.FTP, Sensitivity.AGGREGATED, Permission.VIEW),
            "institution": (ModuleScope.ACCOUNT, Sensitivity.RESTRICTED, Permission.VIEW),
            "markets": (ModuleScope.MARKETS, Sensitivity.PUBLISHED, Permission.VIEW),
            "positions": (ModuleScope.RISK, Sensitivity.CONFIDENTIAL, Permission.VIEW),
            "reports": (ModuleScope.REGULATORY, Sensitivity.PUBLISHED, Permission.VIEW),
            "risk": (ModuleScope.RISK, Sensitivity.CONFIDENTIAL, Permission.VIEW),
            "submissions": (ModuleScope.REGULATORY, Sensitivity.PUBLISHED, Permission.VIEW),
        }
        requirements = (requirement_by_root[root],)
    return normalized, requirements


def _route_title(route: str) -> str:
    exact = {
        "/fx": "Foreign Exchange",
        "/irr": "IRRBB",
        "/liquidity": "Liquidity",
        "/liquidity/stress": "Liquidity stress scenarios",
        "/reports": "Reports",
    }
    if route in exact:
        return exact[route]
    return route.rsplit("/", 1)[-1].replace("-", " ").title()


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
        grant_reason_category=GrantReasonCategory(binding.grant_reason_category),
        grant_reason=binding.grant_reason,
        grant_reference=binding.grant_reference,
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


def _reason_text(payload: ScopedGrantInput) -> str:
    return payload.reason_detail or payload.reason_category.value.replace("_", " ")


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
            reason=_reason_text(payload),
            reason_category=payload.reason_category,
            reference=payload.reference,
            valid_until=payload.valid_until,
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


def _access_request_read(
    request: AuthorizationAccessRequest,
    *,
    user: User,
    bank: Bank | None,
) -> AccessRequestRead:
    return AccessRequestRead(
        id=request.id,
        requester_user_id=user.id,
        requester_name=user.display_name or user.email,
        requester_email=user.email,
        route=request.route,
        page_title=request.page_title,
        institution_scope=(
            InstitutionScope.INSTITUTION if bank is not None else InstitutionScope.ORGANIZATION
        ),
        institution_id=bank.id if bank is not None else None,
        institution_name=bank.name if bank is not None else None,
        module_scope=ModuleScope(request.module_scope),
        sensitivity_scope=Sensitivity(request.sensitivity_scope),
        permission=Permission(request.permission),
        reason_category=GrantReasonCategory(request.reason_category),
        reason_detail=request.reason_detail,
        reference=request.reference,
        valid_until=request.valid_until,
        status=cast(Literal["pending", "approved", "rejected"], request.status),
        requested_at=request.created_at,
    )


def _pending_access_request(
    db: DbSession,
    *,
    requester: User,
    route: str,
    institution_id: str | None,
    payload: AccessRequestCreate,
) -> AuthorizationAccessRequest | None:
    return db.scalar(
        select(AuthorizationAccessRequest).where(
            AuthorizationAccessRequest.organization_id == requester.organization_id,
            AuthorizationAccessRequest.requester_user_id == requester.id,
            AuthorizationAccessRequest.route == route,
            AuthorizationAccessRequest.institution_id.is_(None)
            if institution_id is None
            else AuthorizationAccessRequest.institution_id == institution_id,
            AuthorizationAccessRequest.module_scope == payload.module_scope.value,
            AuthorizationAccessRequest.sensitivity_scope == payload.sensitivity_scope.value,
            AuthorizationAccessRequest.permission == payload.permission.value,
            AuthorizationAccessRequest.status == "pending",
        )
    )


@router.post(
    "/authorization/access-requests",
    response_model=AccessRequestRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAuthorizationAccessRequest",
)
def create_authorization_access_request(
    payload: AccessRequestCreate,
    db: DbSession,
    ctx: Tenant,
) -> AccessRequestRead:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Member identity required.",
        )
    route, allowed_requirements = _route_requirement(payload.route)
    requested_requirement = (
        payload.module_scope,
        payload.sensitivity_scope,
        payload.permission,
    )
    if requested_requirement not in allowed_requirements:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
    user = db.scalar(
        select(User).where(
            User.id == ctx.actor_user_id,
            User.organization_id == ctx.organization_id,
            User.is_active.is_(True),
        )
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
    bank: Bank | None = None
    if payload.institution_id is not None:
        bank = db.scalar(
            select(Bank).where(
                Bank.id == payload.institution_id,
                Bank.organization_id == ctx.organization_id,
            )
        )
        if bank is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
    institution_id = bank.id if bank is not None else None
    existing = _pending_access_request(
        db,
        requester=user,
        route=route,
        institution_id=institution_id,
        payload=payload,
    )
    if existing is not None:
        return _access_request_read(existing, user=user, bank=bank)
    request = AuthorizationAccessRequest(
        organization_id=ctx.organization_id,
        requester_user_id=user.id,
        institution_id=institution_id,
        route=route,
        page_title=_route_title(route),
        module_scope=payload.module_scope.value,
        sensitivity_scope=payload.sensitivity_scope.value,
        permission=payload.permission.value,
        reason_category=payload.reason_category.value,
        reason_detail=payload.reason_detail,
        reference=payload.reference,
        valid_until=payload.valid_until,
        status="pending",
    )
    db.add(request)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        winner = _pending_access_request(
            db,
            requester=user,
            route=route,
            institution_id=institution_id,
            payload=payload,
        )
        if winner is None:
            raise
        return _access_request_read(winner, user=user, bank=bank)
    db.add(
        AuditEvent(
            organization_id=ctx.organization_id,
            actor_user_id=user.id,
            event_type="authorization.access_requested",
            entity_type="authorization_access_request",
            entity_id=str(request.id),
            details={
                "route": request.route,
                "institution_id": request.institution_id,
                "module": request.module_scope,
                "sensitivity": request.sensitivity_scope,
                "permission": request.permission,
                "reason_category": request.reason_category,
                "reason_detail": request.reason_detail,
                "reference": request.reference,
                "valid_until": (request.valid_until.isoformat() if request.valid_until else None),
            },
        )
    )
    db.commit()
    db.refresh(request)
    return _access_request_read(request, user=user, bank=bank)


@router.get(
    "/authorization/access-requests/mine",
    response_model=AccessRequestListRead,
    operation_id="listMyAuthorizationAccessRequests",
)
def list_my_authorization_access_requests(
    db: DbSession,
    ctx: Tenant,
) -> AccessRequestListRead:
    if ctx.actor_user_id is None:
        return AccessRequestListRead(requests=[])
    rows = list(
        db.scalars(
            select(AuthorizationAccessRequest)
            .where(
                AuthorizationAccessRequest.organization_id == ctx.organization_id,
                AuthorizationAccessRequest.requester_user_id == ctx.actor_user_id,
            )
            .order_by(AuthorizationAccessRequest.created_at.desc())
        )
    )
    user = _member_or_404(db, ctx, ctx.actor_user_id)
    banks = {
        bank.id: bank
        for bank in db.scalars(select(Bank).where(Bank.organization_id == ctx.organization_id))
    }
    return AccessRequestListRead(
        requests=[
            _access_request_read(
                row,
                user=user,
                bank=banks[row.institution_id] if row.institution_id else None,
            )
            for row in rows
        ]
    )


@router.get(
    "/authorization/access-requests",
    response_model=AccessRequestListRead,
    operation_id="listAuthorizationAccessRequests",
)
def list_authorization_access_requests(
    db: DbSession,
    ctx: GrantAdminTenant,
) -> AccessRequestListRead:
    rows = list(
        db.scalars(
            select(AuthorizationAccessRequest)
            .where(
                AuthorizationAccessRequest.organization_id == ctx.organization_id,
                AuthorizationAccessRequest.status == "pending",
            )
            .order_by(AuthorizationAccessRequest.created_at)
        )
    )
    users, banks, _ = _presentation_maps(db, ctx.organization_id)
    return AccessRequestListRead(
        requests=[
            _access_request_read(
                row,
                user=users[row.requester_user_id],
                bank=banks[row.institution_id] if row.institution_id else None,
            )
            for row in rows
        ]
    )


@router.post(
    "/authorization/access-requests/{request_id}/approve",
    response_model=BindingCreateResponse,
    operation_id="approveAuthorizationAccessRequest",
)
def approve_authorization_access_request(
    request_id: UUID,
    payload: AccessRequestApprove,
    db: DbSession,
    ctx: GrantAdminTenant,
) -> BindingCreateResponse:
    assert ctx.actor_user_id is not None
    request = db.scalar(
        select(AuthorizationAccessRequest)
        .where(
            AuthorizationAccessRequest.id == request_id,
            AuthorizationAccessRequest.organization_id == ctx.organization_id,
            AuthorizationAccessRequest.status == "pending",
        )
        .with_for_update()
    )
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Access request not found.",
        )
    requested_scope = (
        InstitutionScope.INSTITUTION
        if request.institution_id is not None
        else InstitutionScope.ORGANIZATION
    )
    if (
        payload.institution_scope is not requested_scope
        or payload.institution_id != request.institution_id
        or payload.module_scope.value != request.module_scope
        or payload.sensitivity_scope.value != request.sensitivity_scope
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The grant must preserve the requested institution, module, and sensitivity.",
        )
    try:
        result = grant_administration.create_scoped_grant(
            db,
            organization_id=ctx.organization_id,
            principal_user_id=request.requester_user_id,
            role_bundle=RoleBundle(payload.role_bundle),
            scope=binding_scope(payload),
            actor_user_id=ctx.actor_user_id,
            reason=_reason_text(payload),
            reason_category=payload.reason_category,
            reference=payload.reference,
            valid_until=payload.valid_until,
            expected_authority_sentence=payload.expected_authority_sentence,
            commit=False,
        )
    except (
        grant_administration.GrantAdministrationError,
        authorization.AuthorizationInvariantError,
    ) as exc:
        if isinstance(exc, authorization.AuthorizationInvariantError):
            exc = grant_administration.GrantAdministrationError(str(exc))
        raise grant_conflict(exc) from exc
    request.status = "approved"
    request.resolved_at = result.binding.granted_at
    request.resolved_by_user_id = ctx.actor_user_id
    request.binding_id = result.binding.id
    db.commit()
    db.refresh(result.binding)
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
    "/organization/institutions",
    response_model=InstitutionDirectoryRead,
    operation_id="listOrganizationInstitutions",
)
def list_organization_institutions(
    db: DbSession, ctx: GrantAdminTenant
) -> InstitutionDirectoryRead:
    """Every institution in the organization, for scoping a grant.

    Grant administration is account-plane authority and must not borrow its
    institution catalogue from the operational plane: ``/banks`` filters to the
    institutions the CALLER can view, which is empty for an Owner holding
    Account alone, so the Members composer could only write organization-wide
    grants. This directory is gated on the persisted Org Owner binding like the
    rest of grant administration and lists the whole organization regardless of
    what the owner personally reads.
    """

    banks = db.scalars(
        select(Bank).where(Bank.organization_id == ctx.organization_id).order_by(Bank.name, Bank.id)
    )
    return InstitutionDirectoryRead(
        institutions=[
            InstitutionDirectoryEntryRead(id=bank.id, name=bank.name, short_name=bank.short_name)
            for bank in banks
        ]
    )


@router.get(
    "/organization/institutions/access-request",
    response_model=InstitutionDirectoryRead,
    operation_id="listAccessRequestInstitutions",
)
def list_access_request_institutions(db: DbSession, ctx: Tenant) -> InstitutionDirectoryRead:
    """Public organization structure needed to target an access request."""

    banks = db.scalars(
        select(Bank).where(Bank.organization_id == ctx.organization_id).order_by(Bank.name, Bank.id)
    )
    return InstitutionDirectoryRead(
        institutions=[
            InstitutionDirectoryEntryRead(id=bank.id, name=bank.name, short_name=bank.short_name)
            for bank in banks
        ]
    )


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
