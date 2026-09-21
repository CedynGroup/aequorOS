from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Final, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.authorization import (
    ConditionCheck,
    InstitutionScope,
    Module,
    Permission,
    ResourceLocator,
    Sensitivity,
)
from app.core.config import get_settings
from app.core.observability import authorization_denied, cross_tenant_attempt
from app.db.session import get_sessionmaker
from app.integrations.storage.base import ObjectStorage
from app.integrations.storage.s3 import get_object_storage
from app.models import Bank, Organization, RegulatoryPackage, User

# Declares a `bearerAuth` (HTTP bearer) security scheme in OpenAPI; auto_error=False so
# we raise our own 401 (with WWW-Authenticate) instead of FastAPI's default 403.
_bearer_scheme = HTTPBearer(auto_error=False, description="App JWT access token")


@dataclass(frozen=True)
class TenantContext:
    # The platform tenant identifier (OR-XXXXXXXX) — the organizations PK.
    organization_id: str
    actor_user_id: UUID | None = None
    roles: tuple[str, ...] = ()
    # Present on normal app tokens and compared with users.authorization_version
    # before their role claims are accepted. Integration keys and impersonation
    # use separate credential lifecycles and leave this unset.
    authorization_version: int | None = None
    # Present only for the integration-key credential branch. A legacy key has
    # no bank target and therefore cannot satisfy machine ingest authorization.
    integration_key_id: UUID | None = None
    integration_key_bank_id: str | None = None
    # Set ONLY under operator act-as-examiner impersonation: the originating
    # inspector session id. Its presence marks the principal as a read-only
    # operator view (actor_user_id is None — the actor is staff, not a tenant
    # user). RLS still pins to ``organization_id``, so a single-tenant view.
    impersonation_context: str | None = None
    # The email of the operator acting as examiner (impersonation only) —
    # provenance for audit; never a tenant identity.
    actor_operator: str | None = None


@dataclass(frozen=True)
class LiquidityMonitoringAccess:
    ctx: TenantContext
    bank: Bank


@dataclass(frozen=True)
class IntegrationPushAccess:
    ctx: TenantContext
    bank: Bank


@dataclass(frozen=True)
class InstitutionPermissionAccess:
    ctx: TenantContext
    bank: Bank


# HTTP methods the boundary treats as state-changing. GET/HEAD/OPTIONS are the
# safe set; everything else must justify itself against
# ``IMPERSONATION_READ_ONLY_ROUTES`` before an impersonated session may reach it.
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# The ONLY routes an act-as-examiner (impersonated) session may reach with an
# unsafe HTTP method. An entry is ``(method, templated route path)`` and is a
# promise that the endpoint COMPUTES AND RETURNS while persisting nothing — a
# POST purely because its input does not fit in a query string.
#
# Adding an entry here is an explicit, reviewable decision to carve a hole in the
# absolute "an impersonated session may never mutate" invariant. Before adding
# one, read the service function and confirm it issues no INSERT/UPDATE/DELETE
# and no ``commit``. ``tests/api/test_impersonation_boundary.py`` pins this set.
IMPERSONATION_READ_ONLY_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # Scenario workbench what-if: ``analysis_workbench.run_analysis`` is
        # documented and verified as "Writes nothing." Saving an analysis is a
        # SEPARATE route (…/analyses) and is analyst-gated.
        ("POST", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analysis"),
    }
)

INTEGRATION_KEY_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/banks/{bank_id}/push-batches"),
        ("POST", "/api/v1/banks/{bank_id}/push-batches/{push_batch_id}/records"),
        ("POST", "/api/v1/banks/{bank_id}/push-batches/{push_batch_id}/commit"),
        ("GET", "/api/v1/banks/{bank_id}/push-batches/{push_batch_id}"),
    }
)


def refuse_impersonated_mutation(request: Request, principal: TenantContext) -> None:
    """Refuse any unsafe-method request made under an impersonated session.

    THE structural enforcement of the read-only impersonation invariant. It runs
    at the authentication boundary (``get_current_principal``) and again when a
    tenant DB session is opened (``get_tenant_db_session``), so it does not
    depend on a route author choosing the right ``ctx`` dependency: an operator
    act-as-examiner token cannot POST/PUT/PATCH/DELETE *anything* the exemption
    set does not name, whatever the route declares.

    Fails CLOSED: if the route cannot be identified from the request scope, an
    unsafe method is refused rather than admitted.
    """
    if principal.impersonation_context is None:
        return
    method = request.method.upper()
    if method not in _UNSAFE_METHODS:
        return
    route_path = getattr(request.scope.get("route"), "path", None)
    if route_path is not None and (method, route_path) in IMPERSONATION_READ_ONLY_ROUTES:
        return
    authorization_denied(
        reason="impersonation_read_only",
        method=method,
        route=route_path,
        organization_id=principal.organization_id,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Impersonation sessions are read-only; this action is not permitted.",
    )


def get_current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> TenantContext:
    """Authenticate a request by verifying its bearer access token (zero-trust).

    The tenant + user + roles come from the *verified* token claims, never from a
    header a caller can spoof. This is the auth boundary the API depends on — and
    therefore where the read-only impersonation invariant is enforced, for every
    authenticated route, before any handler or narrower ``ctx`` dependency runs.
    """
    principal = _authenticate_principal(credentials)
    if principal.integration_key_id is not None:
        route_path = getattr(request.scope.get("route"), "path", None)
        if (request.method.upper(), route_path) not in INTEGRATION_KEY_ROUTES:
            authorization_denied(
                reason="integration_key_route_not_allowed",
                method=request.method.upper(),
                route=route_path,
                organization_id=principal.organization_id,
                integration_key_id=str(principal.integration_key_id),
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Integration keys are valid only for API Push.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    if principal.authorization_version is not None:
        session = get_sessionmaker()()
        session.info["organization_id"] = principal.organization_id
        try:
            validate_tenant_context(session, principal)
        finally:
            session.close()
    refuse_impersonated_mutation(request, principal)
    return principal


def _authenticate_principal(
    credentials: HTTPAuthorizationCredentials | None,
) -> TenantContext:
    """Resolve the verified principal from the bearer credential (no policy)."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Integration keys (aeq_live_…) are an alternate bearer credential: bank
    # middleware authenticates as its service account. Resolved pre-tenant
    # (global hash lookup), then validated like any principal downstream.
    # Local import: the service imports TenantContext from this module.
    from app.services.integration_keys import (  # noqa: PLC0415 - break the deps<->service cycle
        authenticate_key,
        looks_like_integration_key,
    )

    if looks_like_integration_key(credentials.credentials):
        session = get_sessionmaker()()
        try:
            return authenticate_key(session, credentials.credentials)
        finally:
            session.close()
    # Operator act-as-examiner impersonation (a THIRD bearer credential, tried
    # before the normal access-token decode). FAILS CLOSED: when the dedicated
    # secret is unset, this branch is skipped entirely, so no impersonation is
    # possible. `decode_impersonation_token` returns None for anything that is
    # not an impersonation token (a normal access token verifies against a
    # DIFFERENT secret and lands here as None) so the request falls through to
    # the existing decode with no regression; it raises only for a token that
    # IS an impersonation token but is expired/invalid.
    impersonation_secret = get_settings().auth.impersonation_jwt_secret
    if impersonation_secret:
        try:
            impersonation_claims = security.decode_impersonation_token(
                credentials.credentials,
                secret=impersonation_secret,
            )
        except security.AuthError as exc:  # expired/invalid impersonation token
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token.",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        if impersonation_claims is not None:
            # The role is PINNED to examiner here, never taken from the claim:
            # the tenant API decides what an impersonation session may do, and
            # examiner sits in no mutation ladder (read everything, mutate
            # nothing). actor_user_id stays None — the actor is an operator.
            return TenantContext(
                organization_id=str(impersonation_claims["org"]),
                actor_user_id=None,
                roles=("examiner",),
                impersonation_context=str(impersonation_claims["session_id"]),
                actor_operator=str(impersonation_claims["act_operator"]),
            )
    try:
        claims = security.decode_token(credentials.credentials, expected_type="access")
    except security.AuthConfigError as exc:  # signing secret unset — fail closed
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        ) from exc
    except security.AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return TenantContext(
        organization_id=str(claims["org"]),
        actor_user_id=UUID(claims["sub"]),
        roles=tuple(claims.get("roles", ())),
        authorization_version=int(claims["authv"]),
    )


def require_role(minimum: str):  # noqa: ANN201 - returns a FastAPI dependency callable
    """Dependency factory: 403 unless the caller holds ``minimum`` (or higher)."""

    def _dependency(
        ctx: Annotated[TenantContext, Depends(get_current_principal)],
    ) -> TenantContext:
        if not security.has_role(list(ctx.roles), minimum):
            authorization_denied(
                reason="insufficient_role",
                required_role=minimum,
                held_roles=",".join(ctx.roles),
                organization_id=ctx.organization_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the '{minimum}' role or higher.",
            )
        return ctx

    # Name the dependency after the role it enforces. The route table is the only
    # place the mutation-guard invariant can be checked wholesale (see
    # ``tests/api/test_impersonation_boundary.py``), and it classifies routes by
    # dependency name — an anonymous ``_dependency`` closure is indistinguishable
    # from every other factory-produced dependency.
    _dependency.__name__ = f"require_role_{minimum}"
    return _dependency


# Dependencies that make a route a guarded mutation. ``examiner``/``viewer``
# remain read roles; scoped Account administration and the explicit
# mutation-role dependencies are the recognized write gates.
MUTATION_ROLE_DEPENDENCY_NAMES: frozenset[str] = frozenset(
    {
        "require_account_administration",
        "require_integration_push_ingest",
        "require_capital_run",
        "require_capital_plan_write",
        "require_capital_plan_approve",
        "require_ilaap_refresh",
        "require_icaap_create",
        "require_icaap_edit",
        "require_icaap_pillar2_approve",
        "require_icaap_addon_approve",
        "require_icaap_audit_review",
        "require_icaap_capital_plan_propose",
        # P3 filing plane: a stage decision, a freeze, a post-freeze send-back,
        # a review-chain approval and a ¶82 disclosure approval are all writes.
        "require_icaap_stage_decision",
        "require_icaap_freeze",
        "require_icaap_review",
        "require_icaap_workflow_approve",
        "require_icaap_disclosure_approve",
        "require_icaap_ai_draft",
        "require_ai_settings_administration",
        "require_fx_run",
        "require_grant_administration",
        "get_scoped_mutation_tenant_context",
        "require_package_validate",
        "require_package_edit",
        "require_package_export",
        "require_package_approve",
        "require_package_submit",
        # The filing review chain: a stage decision is a write, whichever
        # authority the stage names.
        "require_package_stage_decision",
        "require_role_admin",
        "require_role_approver",
        "require_role_analyst",
    }
)


def get_tenant_context(
    principal: Annotated[TenantContext, Depends(get_current_principal)],
) -> TenantContext:
    """Tenant context for a READ request — derived from the verified bearer token.

    (Was demo header-trust; now every request is authenticated by JWT signature.)

    This dependency applies NO role check: it admits ``viewer`` and ``examiner``.
    A route that changes state must declare :data:`MutationTenant` (or
    :data:`ApproverTenant`) — declaring :data:`Tenant` on a mutation is the P0-2
    defect and hands ``viewer`` a write. Impersonation is refused structurally
    upstream (``refuse_impersonated_mutation``), but the ROLE ladder is not: it
    is the route's declaration that carries it.
    """
    return principal


def get_mutation_tenant_context(
    principal: Annotated[TenantContext, Depends(get_current_principal)],
) -> TenantContext:
    """Tenant context for a mutating request: requires an acting user AND the
    ``analyst`` role (or higher) — the RBAC write side of the model.

    This gate makes ``viewer`` (and ``examiner``) read-only ONLY on the routes
    that declare it. It is not a boundary control and never was: a route that
    declares :data:`Tenant` instead never reaches this function. The invariant
    that IS enforced at the boundary — no mutation under impersonation — lives in
    :func:`refuse_impersonated_mutation`; the guard test
    ``tests/api/test_impersonation_boundary.py`` walks the whole route table to
    keep the role side honest.
    """
    # Act-as-examiner impersonation is strictly read-only: an operator viewing a
    # tenant may NEVER mutate its state. Refuse here — before the actor/role
    # checks — so the reason is explicit and unmistakable. Defense in depth: the
    # boundary already refused (``refuse_impersonated_mutation``) and the role is
    # pinned to ``examiner``, which sits in no mutation ladder.
    if principal.impersonation_context is not None:
        authorization_denied(
            reason="impersonation_read_only_mutation",
            organization_id=principal.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Impersonation sessions are read-only; this action is not permitted.",
        )
    if principal.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    if not security.has_role(list(principal.roles), "analyst"):
        authorization_denied(
            reason="insufficient_role",
            required_role="analyst",
            held_roles=",".join(principal.roles),
            organization_id=principal.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the 'analyst' role or higher.",
        )
    return principal


def get_scoped_mutation_tenant_context(
    principal: Annotated[TenantContext, Depends(get_current_principal)],
) -> TenantContext:
    """Require an interactive human for a service-enforced scoped mutation.

    This dependency deliberately checks no scalar role. The product service
    resolves the target resource and requires the exact stored binding before
    any write, enqueue, or network side effect. Its named presence keeps the
    route-table mutation guard honest while later module cutovers share one
    principal boundary.
    """

    if principal.impersonation_context is not None:
        authorization_denied(
            reason="impersonation_read_only_mutation",
            organization_id=principal.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Impersonation sessions are read-only; this action is not permitted.",
        )
    if principal.actor_user_id is None or principal.authorization_version is None:
        authorization_denied(
            reason="human_scoped_binding_required",
            organization_id=principal.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires an active scoped binding.",
        )
    return principal


def get_tenant_db_session(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
) -> Iterator[Session]:
    # Second, independent gate on the read-only impersonation invariant. Every
    # data-touching route takes this dependency, so even a route that somehow
    # resolved a principal without the boundary check cannot obtain a writable
    # session under an impersonated token. Cheap (a frozenset lookup) and it
    # keeps the invariant true of the SESSION, not just of the auth path.
    refuse_impersonated_mutation(request, ctx)
    session = get_sessionmaker()()
    session.info["organization_id"] = ctx.organization_id
    try:
        if ctx.authorization_version is None:
            validate_tenant_context(session, ctx)
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def validate_tenant_context(session: Session, ctx: TenantContext) -> None:
    organization_id = session.scalar(
        select(Organization.id).where(Organization.id == ctx.organization_id)
    )
    if organization_id is None:
        # The token names an organization this session cannot see. Either the org
        # was deleted, or a token is being presented against the wrong tenant.
        cross_tenant_attempt(
            reason="organization_not_visible",
            organization_id=ctx.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context is not valid.",
        )

    # Act-as-examiner impersonation: the actor is an OPERATOR, not a tenant user,
    # so there is no ``users`` row to validate — but the org must still exist
    # (checked above) and RLS still pins the session to that single org. Skip the
    # user check; keep the org check.
    if ctx.impersonation_context is not None:
        return

    if ctx.actor_user_id is None:
        return

    actor = session.scalar(
        select(User).where(
            User.id == ctx.actor_user_id,
            User.organization_id == ctx.organization_id,
            User.is_active.is_(True),
        )
    )
    if actor is None:
        # A verified token whose subject is not an active user of the org it
        # claims. Deactivation is the benign explanation; a replayed or
        # cross-tenant token is the one worth seeing.
        cross_tenant_attempt(
            reason="actor_not_active_in_organization",
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context is not valid.",
        )
    if (
        ctx.authorization_version is not None
        and ctx.authorization_version != actor.authorization_version
    ):
        authorization_denied(
            reason="stale_authorization_version",
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
            token_authorization_version=ctx.authorization_version,
            current_authorization_version=actor.authorization_version,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session authorization is stale. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )


DbSession = Annotated[Session, Depends(get_tenant_db_session)]


def resolve_tenant_bank(
    request: Request,
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
) -> Bank | None:
    """Resolve a path or query ``bank_id`` inside the authenticated tenant.

    Bank existence is tenant-confidential. An unknown identifier and an
    identifier owned by another organization therefore produce the same 404
    before any module entitlement or scoped permission dependency runs.
    """
    from app.services.public_ids import normalize_public_id  # noqa: PLC0415

    raw_bank_id = request.path_params.get("bank_id")
    if raw_bank_id is None:
        raw_bank_id = request.query_params.get("bank_id")
    if raw_bank_id is None:
        return None

    bank_id = normalize_public_id(str(raw_bank_id))
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        cross_tenant_attempt(
            reason="bank_not_visible_to_tenant",
            organization_id=ctx.organization_id,
            bank_id=bank_id,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


TenantBank = Annotated[Bank | None, Depends(resolve_tenant_bank)]

BANK_ROUTE_DEPENDENCIES: Final = (Depends(resolve_tenant_bank),)


def _require_organization_account_permission(
    db: Session,
    ctx: TenantContext,
    *,
    permission: str,
    surface: str,
    detail: str,
) -> TenantContext:
    from app.core.authorization import Module, Sensitivity  # noqa: PLC0415

    return _require_organization_permission(
        db,
        ctx,
        module=Module.ACCOUNT,
        sensitivity=Sensitivity.RESTRICTED,
        permission=permission,
        surface=surface,
        detail=detail,
    )


def _require_organization_permission(  # noqa: PLR0913 - the complete policy tuple is explicit
    db: Session,
    ctx: TenantContext,
    *,
    module: Module,
    sensitivity: Sensitivity,
    permission: str,
    surface: str,
    detail: str,
    conditions: tuple[ConditionCheck, ...] = (),
) -> TenantContext:
    """Require one complete ORGANIZATION-wide binding on a module.

    The same shape as the institution check, for authority that is not about
    one bank: internal audit is an organisation function, and an auditor who
    had to hold a per-institution Capital grant to record a review would be
    holding the authority they are supposed to be independent of.
    """
    from app.core.authorization import (  # noqa: PLC0415 - avoid deps/service cycle
        InstitutionScope,
        Permission,
        PrincipalLocator,
        PrincipalType,
        ResourceLocator,
    )
    from app.services import authorization as authorization_service  # noqa: PLC0415

    required_permission = Permission(permission)
    resource = ResourceLocator(
        ctx.organization_id,
        InstitutionScope.ORGANIZATION,
        None,
        module,
        sensitivity,
    )
    if ctx.actor_user_id is None or ctx.authorization_version is None:
        authorization_denied(
            reason="human_account_binding_required",
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id) if ctx.actor_user_id is not None else None,
            principal_type=(
                "machine"
                if ctx.actor_user_id is not None
                else "operator_impersonation"
                if ctx.impersonation_context is not None
                else "unknown"
            ),
            permission=required_permission.value,
            surface=surface,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    principal = PrincipalLocator(
        ctx.organization_id,
        ctx.actor_user_id,
        PrincipalType.HUMAN,
    )
    try:
        decision = authorization_service.evaluate_permission(
            db,
            principal,
            required_permission,
            resource,
            conditions=conditions,
        )
    except Exception as exc:  # noqa: BLE001 - enforcement must deny on evaluator failure
        authorization_service.record_binding_evaluation_failure(
            principal,
            required_permission,
            resource,
            surface=surface,
            error=exc,
        )
        authorization_denied(
            reason="binding_evaluation_failed",
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
            permission=required_permission.value,
            surface=surface,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail) from exc
    authorization_service.record_binding_decision(
        decision,
        surface=surface,
        severity="info" if decision.allowed else "warning",
    )
    if not decision.allowed:
        authorization_denied(
            reason=decision.reason,
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
            permission=required_permission.value,
            surface=surface,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return ctx


def require_account_administration(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(get_current_principal)],
) -> TenantContext:
    """Require one complete organization-wide Account/restricted admin binding."""

    return _require_organization_account_permission(
        db,
        ctx,
        permission="administer",
        surface="account_administration",
        detail="This action requires scoped Account administration authority.",
    )


def require_account_directory_view(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(get_current_principal)],
) -> TenantContext:
    """Require one complete organization-wide Account/restricted view binding."""

    return _require_organization_account_permission(
        db,
        ctx,
        permission="view",
        surface="organization_user_directory",
        detail="This action requires scoped Account directory view authority.",
    )


def require_grant_administration(
    ctx: Annotated[TenantContext, Depends(get_current_principal)],
    db: DbSession,
) -> TenantContext:
    """Require the explicit active Org Owner binding from issue #127.

    Scalar account-admin claims are deliberately ignored here. A scoped Account
    administrator may configure account surfaces, but only the persisted owner
    binding may create or revoke another person's authority.
    """

    from app.core.authorization import (  # noqa: PLC0415 - avoid deps/service cycle
        InstitutionScope,
        Module,
        Permission,
        PrincipalLocator,
        PrincipalType,
        ResourceLocator,
        RoleBundle,
        Sensitivity,
    )
    from app.services import authorization as authorization_service  # noqa: PLC0415

    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    decision = authorization_service.evaluate_permission(
        db,
        PrincipalLocator(ctx.organization_id, ctx.actor_user_id, PrincipalType.HUMAN),
        Permission.ADMINISTER,
        ResourceLocator(
            ctx.organization_id,
            InstitutionScope.ORGANIZATION,
            None,
            Module.ACCOUNT,
            Sensitivity.RESTRICTED,
        ),
    )
    owner_matched = any(
        trace.matched and trace.role_bundle is RoleBundle.ORG_OWNER
        for trace in decision.binding_trace
    )
    if not decision.allowed or not owner_matched:
        authorization_denied(
            reason="org_owner_binding_required",
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires Organization Owner authority.",
        )
    return ctx


def require_liquidity_monitoring_view(
    request: Request,
    db: DbSession,
    ctx: Tenant,
) -> LiquidityMonitoringAccess:
    """Require one exact active LIQ/confidential view binding for the bank."""

    return _require_liquidity_view(
        request,
        db,
        ctx,
        sensitivity=Sensitivity.CONFIDENTIAL,
        surface="liquidity_monitoring",
        denial_detail="Liquidity Monitoring access requires an active scoped binding.",
    )


def _require_liquidity_view(  # noqa: PLR0913 - complete authorization sentence
    request: Request,
    db: DbSession,
    ctx: Tenant,
    *,
    sensitivity: Sensitivity,
    surface: str,
    denial_detail: str = "Liquidity access requires an active scoped binding.",
    prefetch: bool = False,
) -> LiquidityMonitoringAccess:
    from app.core.authorization import Module, Permission  # noqa: PLC0415
    from app.services import scoped_authorization  # noqa: PLC0415

    require_permission = (
        scoped_authorization.require_bank_permission_prefetched
        if prefetch
        else scoped_authorization.require_bank_permission
    )
    bank = require_permission(
        db,
        ctx,
        str(request.path_params.get("bank_id", "")),
        permission=Permission.VIEW,
        module=Module.LIQUIDITY,
        sensitivity=sensitivity,
        surface=surface,
        denial_detail=denial_detail,
    )
    return LiquidityMonitoringAccess(ctx=ctx, bank=bank)


def require_integration_push_ingest(
    request: Request,
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> IntegrationPushAccess:
    """Require one exact active machine DATA/restricted ingest binding."""

    from app.core.authorization import (  # noqa: PLC0415 - avoid deps/service cycle
        InstitutionScope,
        Module,
        Permission,
        PrincipalLocator,
        PrincipalType,
        ResourceLocator,
        Sensitivity,
    )
    from app.services import authorization as authorization_service  # noqa: PLC0415
    from app.services import integration_keys  # noqa: PLC0415
    from app.services.public_ids import normalize_public_id  # noqa: PLC0415

    requested_bank_id = normalize_public_id(str(request.path_params.get("bank_id", "")))
    if ctx.actor_user_id is None or ctx.authorization_version is not None:
        authorization_denied(
            reason="integration_push_machine_binding_required",
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id) if ctx.actor_user_id is not None else None,
            bank_id=requested_bank_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Push requires a bank-scoped integration key.",
        )
    if ctx.integration_key_bank_id != requested_bank_id:
        cross_tenant_attempt(
            reason="integration_key_bank_mismatch",
            organization_id=ctx.organization_id,
            bank_id=requested_bank_id,
            credential_bank_id=ctx.integration_key_bank_id,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")

    # Locking the key serializes this request with revocation. The lock remains
    # held through the route-boundary commit, so a key cannot be revoked after
    # authorization but before its storage/database mutation completes.
    integration_keys.lock_authenticated_key(db, ctx)
    if bank is None:  # pragma: no cover - integration push routes always carry bank_id
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")

    principal = PrincipalLocator(
        ctx.organization_id,
        ctx.actor_user_id,
        PrincipalType.MACHINE,
    )
    resource = ResourceLocator(
        ctx.organization_id,
        InstitutionScope.INSTITUTION,
        bank.id,
        Module.DATA,
        Sensitivity.RESTRICTED,
    )
    try:
        decision = authorization_service.evaluate_permission(
            db,
            principal,
            Permission.INGEST,
            resource,
        )
        authorization_service.record_binding_decision(
            decision,
            surface="integration_push",
            severity="info" if decision.allowed else "warning",
        )
    except Exception as exc:  # noqa: BLE001 - enforcement must deny on evaluator failure
        authorization_service.record_binding_evaluation_failure(
            principal,
            Permission.INGEST,
            resource,
            surface="integration_push",
            error=exc,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Push requires an active bank-scoped machine binding.",
        ) from exc
    if not decision.allowed:
        authorization_denied(
            reason=decision.reason,
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
            bank_id=bank.id,
            module=Module.DATA.value,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Push requires an active bank-scoped machine binding.",
        )
    return IntegrationPushAccess(ctx=ctx, bank=bank)


def _require_institution_permission(  # noqa: PLR0913 - complete policy tuple is explicit
    db: DbSession,
    ctx: TenantContext,
    bank: Bank | None,
    *,
    module: Module,
    sensitivity: Sensitivity,
    permission: Permission,
    surface: str,
    detail: str,
    conditions: tuple[ConditionCheck, ...] = (),
) -> InstitutionPermissionAccess:
    """Require one complete active binding for an exact tenant institution."""

    from app.services import authorization as authorization_service  # noqa: PLC0415

    if bank is None:  # pragma: no cover - institution permissions are bank-scoped
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    if ctx.actor_user_id is None:
        authorization_denied(
            reason="scoped_binding_principal_required",
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            module=module.value,
            permission=permission.value,
            surface=surface,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    principal = authorization_service.principal_locator(ctx)
    resource = ResourceLocator(
        ctx.organization_id,
        InstitutionScope.INSTITUTION,
        bank.id,
        module,
        sensitivity,
    )
    try:
        decision = authorization_service.evaluate_permission(
            db,
            principal,
            permission,
            resource,
            conditions=conditions,
        )
    except Exception as exc:  # noqa: BLE001 - enforcement must deny on evaluator failure
        authorization_service.record_binding_evaluation_failure(
            principal,
            permission,
            resource,
            surface=surface,
            error=exc,
        )
        authorization_denied(
            reason="binding_evaluation_failed",
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
            bank_id=bank.id,
            module=module.value,
            permission=permission.value,
            surface=surface,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail) from exc

    authorization_service.record_binding_decision(
        decision,
        surface=surface,
        severity="info" if decision.allowed else "warning",
    )
    if not decision.allowed:
        authorization_denied(
            reason=decision.reason,
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
            bank_id=bank.id,
            module=module.value,
            permission=permission.value,
            surface=surface,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return InstitutionPermissionAccess(ctx=ctx, bank=bank)


def require_capital_aggregated_view(
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> InstitutionPermissionAccess:
    return _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.CAPITAL,
        sensitivity=Sensitivity.AGGREGATED,
        permission=Permission.VIEW,
        surface="capital_aggregated_view",
        detail="Capital access requires an active scoped binding.",
    )


def require_capital_confidential_view(
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> InstitutionPermissionAccess:
    return _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.CAPITAL,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=Permission.VIEW,
        surface="capital_confidential_view",
        detail="Capital access requires an active scoped binding.",
    )


def require_capital_restricted_view(
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> InstitutionPermissionAccess:
    return _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.CAPITAL,
        sensitivity=Sensitivity.RESTRICTED,
        permission=Permission.VIEW,
        surface="capital_restricted_view",
        detail="Capital assurance access requires an active scoped binding.",
    )


def require_capital_run(
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> InstitutionPermissionAccess:
    return _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.CAPITAL,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=Permission.RUN,
        surface="capital_run",
        detail="Running Capital calculations requires an active scoped binding.",
    )


@dataclass(frozen=True)
class IcaapAccess:
    """A resolved ICAAP principal: the tenant, the institution, and how it got in."""

    ctx: TenantContext
    bank: Bank
    #: True only on the impersonated-examiner read branch, which reads no
    #: binding and is limited to cycles that have been frozen.
    examiner: bool = False


_ICAAP_DETAIL = "ICAAP access requires an active scoped binding."


def _require_icaap_access(  # noqa: PLR0913 - the complete policy tuple is explicit
    request: Request,
    db: Session,
    ctx: TenantContext,
    *,
    permission: Permission,
    surface: str,
    allow_examiner: bool = False,
    conditions: tuple[ConditionCheck, ...] = (),
) -> IcaapAccess:
    """Institution, then licence class, then authority.

    The order is the policy. A bank outside the ICAAP regime answers 404 rather
    than 403 — even when the caller holds a perfectly good capital binding —
    because the surface does not exist for that licence class. Only after that
    does a missing binding become 403, because the caller's own institution
    existing is not a secret from them.
    """
    from app.services.icaap import guards  # noqa: PLC0415 - avoid deps/service cycle
    from app.services.public_ids import normalize_public_id  # noqa: PLC0415

    bank_id = normalize_public_id(str(request.path_params.get("bank_id", "")))
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        cross_tenant_attempt(
            reason="bank_not_visible_to_tenant",
            organization_id=ctx.organization_id,
            bank_id=bank_id,
            module=Module.CAPITAL.value,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    guards.require_bank_class(db, bank)
    if ctx.impersonation_context is not None:
        if not allow_examiner:
            authorization_denied(
                reason="impersonation_read_only_mutation",
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                module=Module.CAPITAL.value,
                permission=permission.value,
                surface=surface,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_ICAAP_DETAIL)
        guards.require_examiner(ctx)
        return IcaapAccess(ctx=ctx, bank=bank, examiner=True)
    access = _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.CAPITAL,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=permission,
        surface=surface,
        detail=_ICAAP_DETAIL,
        conditions=conditions,
    )
    return IcaapAccess(ctx=access.ctx, bank=access.bank)


def require_icaap_view(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    return _require_icaap_access(
        request, db, ctx, permission=Permission.VIEW, surface="icaap_view", allow_examiner=True
    )


def require_icaap_create(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    return _require_icaap_access(
        request, db, ctx, permission=Permission.CREATE, surface="icaap_create"
    )


def require_icaap_edit(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    return _require_icaap_access(request, db, ctx, permission=Permission.EDIT, surface="icaap_edit")


def require_icaap_export(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    return _require_icaap_access(
        request, db, ctx, permission=Permission.EXPORT, surface="icaap_export"
    )


def require_ai_settings_administration(
    ctx: Annotated[TenantContext, Depends(get_current_principal)],
    db: DbSession,
) -> TenantContext:
    """Consenting to send a tenant's data to an external service is the Owner's.

    Deliberately the SAME authority as grant administration, not scoped Account
    administration: an account administrator configures account surfaces, but
    deciding that this organisation's figures may leave the platform is the one
    decision its Owner makes personally.
    """
    return require_grant_administration(ctx, db)


def require_icaap_ai_draft(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    """Requesting or deciding an AI draft: ICAAP EDIT, plus the egress gates.

    The deployment gate runs FIRST and answers 404, before any tenant lookup: a
    platform with AI switched off should not have an AI surface that answers
    differently depending on who asks. The tenant gates answer 403 with their
    code, because a tenant CAN act on "your Owner has not switched this on".
    """
    from app.services.ai import gates as ai_gates  # noqa: PLC0415 - avoid deps/service cycle

    if not ai_gates.deployment_gate().allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    access = _require_icaap_access(
        request, db, ctx, permission=Permission.EDIT, surface="icaap_ai_draft"
    )
    from app.services.icaap import ai_prompt  # noqa: PLC0415 - avoid deps/service cycle

    decision = ai_gates.evaluate(
        db,
        ctx.organization_id,
        "icaap_drafting",
        phase="enqueue",
        prompt_version=ai_prompt.PROMPT_VERSION,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": decision.code, "message": decision.message},
        )
    return access


def _icaap_path_uuid(request: Request, name: str) -> UUID | None:
    raw = request.path_params.get(name)
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def require_icaap_pillar2_approve(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    """Approving a Pillar 2 figure: CAP approve, and not its own author.

    The maker-checker condition resolves the author of the revision BEING
    approved, so the refusal is an authorization decision with a trace rather
    than a check buried in the service. The service re-checks it as well,
    because two requests can race between the two.
    """
    from app.services.icaap import pillar2  # noqa: PLC0415 - avoid deps/service cycle
    from app.services.public_ids import normalize_public_id  # noqa: PLC0415

    bank_id = normalize_public_id(str(request.path_params.get("bank_id", "")))
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    cycle_id = _icaap_path_uuid(request, "cycle_id")
    item_id = _icaap_path_uuid(request, "item_id")
    conditions: tuple[ConditionCheck, ...] = ()
    if bank is not None and cycle_id is not None and item_id is not None:
        conditions = pillar2.approval_conditions(db, ctx, bank, cycle_id, item_id)
    return _require_icaap_access(
        request,
        db,
        ctx,
        permission=Permission.APPROVE,
        surface="icaap_pillar2_approve",
        conditions=conditions,
    )


def require_icaap_addon_approve(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    """Confirming or withdrawing a supervisory add-on: CAP approve, four eyes.

    The database also refuses ``confirmed_by = created_by``; this is the layer
    that answers with an authorization decision instead of a constraint error.
    """
    from app.services.icaap import supervisory_addons  # noqa: PLC0415

    addon_id = _icaap_path_uuid(request, "addon_id")
    conditions: tuple[ConditionCheck, ...] = ()
    if addon_id is not None:
        conditions = supervisory_addons.confirmation_conditions(db, ctx, addon_id)
    return _require_icaap_access(
        request,
        db,
        ctx,
        permission=Permission.APPROVE,
        surface="icaap_addon_approve",
        conditions=conditions,
    )


def require_icaap_audit_review(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    """Recording the INDEPENDENT review: ICAAP visibility plus an Audit grant.

    Two separate decisions, in this order. First the caller must be able to see
    this ICAAP at all (CAP/confidential VIEW, and never as an impersonated
    examiner — a supervisor does not record a bank's internal audit). Then they
    must hold an ORGANISATION-wide Audit grant, with the maker-checker condition
    that they are not a participant of this cycle. Requiring Capital EDIT
    instead would mean the reviewer held the authority they are reviewing.
    """
    from app.core.authorization import ConditionKind  # noqa: PLC0415
    from app.models.icaap import IcaapCycle  # noqa: PLC0415
    from app.services.icaap import audit_reviews  # noqa: PLC0415

    access = _require_icaap_access(
        request, db, ctx, permission=Permission.VIEW, surface="icaap_audit_review_view"
    )
    cycle_id = _icaap_path_uuid(request, "cycle_id")
    # A cycle that does not exist must not turn a MISSING AUDIT GRANT into a
    # 404: the caller is told they lack the authority, and the handler reports
    # the missing cycle afterwards.
    cycle = (
        None
        if cycle_id is None
        else db.scalar(
            select(IcaapCycle).where(
                IcaapCycle.id == cycle_id,
                IcaapCycle.organization_id == ctx.organization_id,
                IcaapCycle.bank_id == access.bank.id,
            )
        )
    )
    independent = cycle is None or audit_reviews.independence_conditions_passed(
        db, access, cycle, ctx.actor_user_id
    )
    _require_organization_permission(
        db,
        ctx,
        module=Module.AUDIT,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=Permission.CREATE.value,
        surface="icaap_audit_review",
        detail=(
            "Recording an independent review requires an organisation-wide Audit "
            "grant held by somebody who did not prepare this ICAAP."
        ),
        conditions=(
            ConditionCheck(
                kind=ConditionKind.MAKER_CHECKER,
                passed=independent,
                reason=(
                    "reviewer did not prepare this ICAAP"
                    if independent
                    else "a preparer of this ICAAP cannot record its independent review"
                ),
            ),
        ),
    )
    return access


def require_icaap_capital_plan_propose(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    """Proposing a capital-plan update: ICAAP edit AND the plan's own authority.

    The proposal writes a capital-plan DRAFT, so it must carry the same
    authority a person needs to write one directly — CREATE for a new version,
    EDIT for an existing draft. Without this an ICAAP editor could author a
    capital plan they are not entitled to author.
    """
    from app.services import capital_plan  # noqa: PLC0415 - avoid deps/service cycle

    access = _require_icaap_access(
        request, db, ctx, permission=Permission.EDIT, surface="icaap_capital_plan_propose"
    )
    _require_institution_permission(
        db,
        ctx,
        access.bank,
        module=Module.CAPITAL,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=capital_plan.required_draft_permission(db, ctx, access.bank),
        surface="capital_plan_draft",
        detail="Changing a capital plan requires an active scoped binding.",
    )
    return access


# --- P3 filing plane -------------------------------------------------------
# Each of these resolves the OBJECT before it evaluates authority, because the
# answer depends on the object: who already reviewed this round, who proposed
# this chain, who chose what to publish. The condition reaches the evaluator as
# a ``MAKER_CHECKER`` check, so the refusal lands in the binding trace with its
# reason instead of being a check buried in a service; the service re-checks it
# under the row lock, because two requests can race between the two.


def require_icaap_stage_decision(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    """Deciding a review or approval stage: CAP/confidential, and four eyes.

    The PERMISSION depends on the stage: a review stage takes REVIEW, an
    approval stage takes APPROVE. An approver bundle carries both, a reviewer
    bundle only the first, so a reviewer cannot sign off the stage that makes
    the report ready to freeze.
    """
    from app.services.icaap import workflow as icaap_workflow  # noqa: PLC0415 - avoid a cycle
    from app.services.public_ids import normalize_public_id  # noqa: PLC0415

    bank_id = normalize_public_id(str(request.path_params.get("bank_id", "")))
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    cycle_id = _icaap_path_uuid(request, "cycle_id")
    raw_seq = request.path_params.get("seq")
    try:
        stage_seq = int(str(raw_seq))
    except (TypeError, ValueError):
        stage_seq = None
    conditions: tuple[ConditionCheck, ...] = ()
    permission = Permission.APPROVE
    if bank is not None and cycle_id is not None and stage_seq is not None:
        conditions = icaap_workflow.stage_authority(db, ctx, bank, cycle_id, stage_seq)
        permission = icaap_workflow.stage_permission(db, ctx, bank, cycle_id, stage_seq)
    return _require_icaap_access(
        request,
        db,
        ctx,
        permission=permission,
        surface="icaap_stage_decision",
        conditions=conditions,
    )


def require_icaap_freeze(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    """Freezing: CAP/confidential EDIT, and NOT by anyone who reviewed it (D-030)."""
    from app.services.icaap import workflow as icaap_workflow  # noqa: PLC0415 - avoid a cycle
    from app.services.public_ids import normalize_public_id  # noqa: PLC0415

    bank_id = normalize_public_id(str(request.path_params.get("bank_id", "")))
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    cycle_id = _icaap_path_uuid(request, "cycle_id")
    conditions: tuple[ConditionCheck, ...] = ()
    if bank is not None and cycle_id is not None:
        conditions = icaap_workflow.freeze_authority(db, ctx, bank, cycle_id)
    return _require_icaap_access(
        request, db, ctx, permission=Permission.EDIT, surface="icaap_freeze", conditions=conditions
    )


def require_icaap_review(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    """Sending a frozen ICAAP back: CAP/confidential REVIEW, and NOT by its freezer.

    The mirror of :func:`require_icaap_freeze`'s D-030 condition. Freezing seals
    the filing; sending it back voids the signatures on the filing that was
    sealed. One officer may not hold both halves — which is why this carried no
    ``conditions`` at all until 2026-09-20 and was the least guarded of the
    ICAAP decision dependencies while being the most destructive (audit F1).
    """
    from app.services.icaap import post_freeze  # noqa: PLC0415 - avoid a cycle
    from app.services.public_ids import normalize_public_id  # noqa: PLC0415

    bank_id = normalize_public_id(str(request.path_params.get("bank_id", "")))
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    cycle_id = _icaap_path_uuid(request, "cycle_id")
    conditions: tuple[ConditionCheck, ...] = ()
    if bank is not None and cycle_id is not None:
        conditions = post_freeze.return_authority(db, ctx, bank, cycle_id)
    return _require_icaap_access(
        request,
        db,
        ctx,
        permission=Permission.REVIEW,
        surface="icaap_review",
        conditions=conditions,
    )


def require_icaap_workflow_approve(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    """Approving the bank's own review chain: APPROVE, and not its proposer."""
    from app.services.icaap import workflow_templates  # noqa: PLC0415 - avoid a cycle
    from app.services.public_ids import normalize_public_id  # noqa: PLC0415

    bank_id = normalize_public_id(str(request.path_params.get("bank_id", "")))
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    template_id = _icaap_path_uuid(request, "template_id")
    conditions: tuple[ConditionCheck, ...] = ()
    if bank is not None:
        conditions = workflow_templates.approval_conditions(
            db, IcaapAccess(ctx=ctx, bank=bank), template_id
        )  # pyright: ignore[reportAssignmentType]
    return _require_icaap_access(
        request,
        db,
        ctx,
        permission=Permission.APPROVE,
        surface="icaap_workflow_approve",
        conditions=conditions,
    )


def require_icaap_disclosure_approve(request: Request, db: DbSession, ctx: Tenant) -> IcaapAccess:
    """Approving what the bank publishes of its ICAAP: APPROVE, and four eyes."""
    from app.services.icaap import disclosure as icaap_disclosure  # noqa: PLC0415 - avoid a cycle
    from app.services.public_ids import normalize_public_id  # noqa: PLC0415

    bank_id = normalize_public_id(str(request.path_params.get("bank_id", "")))
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    cycle_id = _icaap_path_uuid(request, "cycle_id")
    conditions: tuple[ConditionCheck, ...] = ()
    if bank is not None:
        conditions = icaap_disclosure.approval_conditions(
            db, IcaapAccess(ctx=ctx, bank=bank), cycle_id
        )  # pyright: ignore[reportAssignmentType]
    return _require_icaap_access(
        request,
        db,
        ctx,
        permission=Permission.APPROVE,
        surface="icaap_disclosure_approve",
        conditions=conditions,
    )


def require_fx_aggregated_view(
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> InstitutionPermissionAccess:
    return _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.FX,
        sensitivity=Sensitivity.AGGREGATED,
        permission=Permission.VIEW,
        surface="fx_aggregated_view",
        detail="FX access requires an active scoped binding.",
    )


def require_fx_run(
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> InstitutionPermissionAccess:
    return _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.FX,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=Permission.RUN,
        surface="fx_run",
        detail="Running FX calculations requires an active scoped binding.",
    )


def require_capital_plan_write(
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> InstitutionPermissionAccess:
    from app.services import capital_plan  # noqa: PLC0415 - avoid deps/service cycle

    if bank is None:  # pragma: no cover - capital plan routes always carry bank_id
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    permission = capital_plan.required_draft_permission(db, ctx, bank)
    return _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.CAPITAL,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=permission,
        surface=f"capital_plan_{permission.value}",
        detail="Changing a capital plan requires an active scoped binding.",
    )


def require_capital_plan_approve(
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> InstitutionPermissionAccess:
    from app.services import capital_plan  # noqa: PLC0415 - avoid deps/service cycle

    if bank is None:  # pragma: no cover - capital plan routes always carry bank_id
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    access = _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.CAPITAL,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=Permission.APPROVE,
        surface="capital_plan_approve",
        detail="Approving a capital plan requires an active scoped binding.",
        conditions=capital_plan.maker_checker_conditions(
            db,
            ctx,
            bank.id,
        ),
    )
    return access


def require_ilaap_refresh(
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> InstitutionPermissionAccess:
    """Require independent CAP/run and LIQ/view decisions before ILAAP storage."""

    capital_access = _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.CAPITAL,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=Permission.RUN,
        surface="ilaap_refresh_capital",
        detail="Refreshing ILAAP requires Capital run and Liquidity view authority.",
    )
    _require_institution_permission(
        db,
        ctx,
        bank,
        module=Module.LIQUIDITY,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=Permission.VIEW,
        surface="ilaap_refresh_liquidity",
        detail="Refreshing ILAAP requires Capital run and Liquidity view authority.",
    )
    return capital_access


def require_liquidity_aggregated_view(
    request: Request,
    db: DbSession,
    ctx: Tenant,
) -> LiquidityMonitoringAccess:
    """Require LIQ/aggregated view authority for one institution."""

    return _require_liquidity_view(
        request,
        db,
        ctx,
        sensitivity=Sensitivity.AGGREGATED,
        surface="liquidity_aggregated_view",
        prefetch=True,
    )


def require_liquidity_confidential_view(
    request: Request,
    db: DbSession,
    ctx: Tenant,
) -> LiquidityMonitoringAccess:
    """Require LIQ/confidential view authority for one institution."""

    return _require_liquidity_view(
        request,
        db,
        ctx,
        sensitivity=Sensitivity.CONFIDENTIAL,
        surface="liquidity_confidential_view",
    )


def get_approver_tenant_context(
    principal: Annotated[TenantContext, Depends(get_mutation_tenant_context)],
) -> TenantContext:
    """Mutation context that additionally requires the ``approver`` role.

    Guards the control actions of the submission pipeline (approval decisions,
    channel submissions, regulator polls, resubmission decisions) — mirroring
    the ORASS split where only the Principal user may submit; analysts prepare,
    approvers release.
    """
    if not security.has_role(list(principal.roles), "approver"):
        authorization_denied(
            reason="insufficient_role",
            required_role="approver",
            held_roles=",".join(principal.roles),
            organization_id=principal.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the 'approver' role or higher.",
        )
    return principal


# ---------------------------------------------------------------------------
# Regulatory package routes: family-dispatching authorization (ICAAP P3, C-4)
# ---------------------------------------------------------------------------
#
# Every package route has always declared a SCALAR dependency: ``Tenant`` to
# read, ``MutationTenant`` to write, ``ApproverTenant`` to approve. That is the
# right answer for a prudential return and the wrong one for an ICAAP, which is
# the bank's own assessment of its capital adequacy and its Board's challenge of
# it.
#
# These dependencies keep BOTH answers, chosen by the package's family:
#
# * ungated family -> the route's existing scalar dependency, called verbatim.
#   Nothing about a BSD or liquidity return changes.
# * gated family   -> a human principal with an exact institution-scoped binding
#   (``family_access``), evaluated on the route's own permission. A missing VIEW
#   answers 404 because the existence of an ICAAP for a date is itself
#   disclosure; a held VIEW with a missing action permission answers 403.
#
# The scalar check runs FIRST when the package is not found, so a viewer hitting
# a mutation route with an unknown id still gets today's 403 rather than a 404
# that would tell them the id is unknown.


@dataclass(frozen=True)
class PackageAccess:
    """A resolved regulatory-package principal, and how it got in."""

    ctx: TenantContext
    bank: Bank
    package: RegulatoryPackage
    #: True when the family's scoped gate decided this, rather than the ladder.
    gated: bool = False
    #: True only on the impersonated-examiner read branch.
    examiner: bool = False


_PACKAGE_DETAIL = "This return requires an active scoped binding for the institution."
type _ScalarGate = Literal["tenant", "mutation", "approver", "scoped"]


def _resolve_package_from_path(
    request: Request, db: Session, ctx: TenantContext
) -> tuple[Bank | None, RegulatoryPackage | None]:
    """The bank and package a package-scoped route names, by whichever key it uses."""
    from app.models import RegulatoryArtifactVersion, RegulatoryPackageArtifact  # noqa: PLC0415
    from app.services.public_ids import normalize_public_id  # noqa: PLC0415

    params = request.path_params
    raw_bank = params.get("bank_id") or params.get("bank_reference") or ""
    bank_id = normalize_public_id(str(raw_bank))
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        return None, None
    package_id = _icaap_path_uuid(request, "package_id")
    if package_id is None:
        artifact_id = _icaap_path_uuid(request, "artifact_id")
        if artifact_id is not None:
            package_id = db.scalar(
                select(RegulatoryPackageArtifact.package_id).where(
                    RegulatoryPackageArtifact.id == artifact_id,
                    RegulatoryPackageArtifact.organization_id == ctx.organization_id,
                )
            )
    if package_id is None:
        version_id = _icaap_path_uuid(request, "version_id")
        if version_id is not None:
            package_id = db.scalar(
                select(RegulatoryArtifactVersion.package_id).where(
                    RegulatoryArtifactVersion.id == version_id,
                    RegulatoryArtifactVersion.organization_id == ctx.organization_id,
                )
            )
    if package_id is None:
        return bank, None
    package = db.scalar(
        select(RegulatoryPackage).where(
            RegulatoryPackage.id == package_id,
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
        )
    )
    return bank, package


def _apply_scalar_gate(ctx: TenantContext, gate: _ScalarGate) -> TenantContext:
    """The route's pre-existing dependency, called verbatim.

    ``"scoped"`` is not a scalar ladder step at all: it is the interactive-human
    boundary every binding-enforced surface shares, and it checks no role. It is
    named here so a package route can declare that its authority is the stored
    binding and nothing else.
    """
    if gate == "tenant":
        return get_tenant_context(ctx)
    if gate == "mutation":
        return get_mutation_tenant_context(ctx)
    if gate == "scoped":
        return get_scoped_mutation_tenant_context(ctx)
    return get_approver_tenant_context(get_mutation_tenant_context(ctx))


def _chain_authority_access(  # noqa: PLR0913 - the complete policy tuple is explicit
    *,
    request_gate: _ScalarGate,
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
    permission: Permission,
    surface: str,
    conditions: tuple[ConditionCheck, ...],
) -> PackageAccess | None:
    """A review-chain decision, authorised by the STORED BINDING first.

    Returns ``None`` to mean "fall through to the route's own dependency,
    unchanged" — which is what makes this a widening rather than a cutover.

    Until 2026-09-20 this act dispatched on the family and, for an ungated one —
    every BSD, liquidity, capital and FX return, i.e. every return a bank
    actually files — consulted only the scalar ladder. An Org Owner's Approver
    grant therefore did nothing, while the dashboard, which projects the control
    from that same grant, offered the button. Fail-open screen over a
    fail-closed server.
    """
    from app.services.regulatory_reporting import family_access  # noqa: PLC0415

    near_miss: tuple[str, str] | None = None
    try:
        scoped = get_scoped_mutation_tenant_context(ctx)
        verdict = family_access.chain_decision_verdict(
            db, scoped, bank, package, permission, surface=surface, conditions=conditions
        )
    except HTTPException:
        # The scoped boundary refused (impersonation, a stale ``authv``). That is
        # not a reason to refuse a caller the old ladder admits, so fall through
        # rather than turning a widening into a narrowing.
        return None
    if verdict.allowed:
        return PackageAccess(ctx=scoped, bank=bank, package=package, gated=True)
    near_miss = verdict.near_miss
    if near_miss is None:
        return None
    # The caller HOLDS a grant for this institution that missed on exactly one
    # scope dimension. Falling through silently would refuse them with the
    # scalar ladder's sentence — "requires the 'analyst' role or higher" — which
    # is about a role they will never hold and sends them to the wrong person.
    # It cost a live debugging round. The ladder is still tried first, in case it
    # admits them anyway; only when it also refuses is its reason replaced by the
    # one they can act on.
    try:
        return PackageAccess(
            ctx=_apply_scalar_gate(ctx, request_gate), bank=bank, package=package, gated=False
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_403_FORBIDDEN:
            raise
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=family_access.near_miss_detail(bank, near_miss),
        ) from exc


def _require_package_access(  # noqa: PLR0913 - the complete policy tuple is explicit
    request: Request,
    db: Session,
    ctx: TenantContext,
    *,
    gate: _ScalarGate,
    permission: Permission,
    surface: str,
    conditions: tuple[ConditionCheck, ...] = (),
    authority: Literal["family", "transmission", "chain"] = "family",
) -> PackageAccess:
    from app.services.regulatory_reporting import family_access  # noqa: PLC0415

    bank, package = _resolve_package_from_path(request, db, ctx)
    if package is None or bank is None:
        # The scalar check first, THEN not-found: a viewer must not learn from a
        # 404 that they got past the write gate.
        _apply_scalar_gate(ctx, gate)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Regulatory package not found."
        )
    if authority == "chain":
        chained = _chain_authority_access(
            request_gate=gate,
            db=db,
            ctx=ctx,
            bank=bank,
            package=package,
            permission=permission,
            surface=surface,
            conditions=conditions,
        )
        if chained is not None:
            return chained
    if authority == "transmission":
        # Filing does not dispatch on family. Every return reaches the regulator
        # through one authority, so there is no ungated branch to fall through.
        scoped = _apply_scalar_gate(ctx, "scoped")
        family_access.require_transmission_authority(
            db, scoped, bank, package, permission, surface=surface, conditions=conditions
        )
        return PackageAccess(ctx=scoped, bank=bank, package=package, gated=True)
    if family_access.gate_for(package.return_family) is None:
        return PackageAccess(
            ctx=_apply_scalar_gate(ctx, gate), bank=bank, package=package, gated=False
        )
    if gate == "tenant":
        family_access.require_view(db, ctx, bank, package)
        if ctx.impersonation_context is not None:
            return PackageAccess(ctx=ctx, bank=bank, package=package, gated=True, examiner=True)
        family_access.require_permission(
            db, ctx, bank, package, permission, surface=surface, conditions=conditions
        )
        return PackageAccess(ctx=ctx, bank=bank, package=package, gated=True)
    # A mutation on a gated family is a scoped human act. No impersonation, an
    # interactive principal with a current ``authv`` — and NO scalar role check,
    # because a scalar role must never satisfy a scoped surface.
    scoped = get_scoped_mutation_tenant_context(ctx)
    family_access.require_permission(
        db, scoped, bank, package, permission, surface=surface, conditions=conditions
    )
    return PackageAccess(ctx=scoped, bank=bank, package=package, gated=True)


def require_package_view(request: Request, db: DbSession, ctx: Tenant) -> PackageAccess:
    return _require_package_access(
        request, db, ctx, gate="tenant", permission=Permission.VIEW, surface="package_view"
    )


def require_package_validate(request: Request, db: DbSession, ctx: Tenant) -> PackageAccess:
    return _require_package_access(
        request,
        db,
        ctx,
        gate="mutation",
        permission=Permission.VALIDATE,
        surface="package_validate",
    )


def require_package_edit(request: Request, db: DbSession, ctx: Tenant) -> PackageAccess:
    return _require_package_access(
        request, db, ctx, gate="mutation", permission=Permission.EDIT, surface="package_edit"
    )


def require_package_export(request: Request, db: DbSession, ctx: Tenant) -> PackageAccess:
    return _require_package_access(
        request, db, ctx, gate="mutation", permission=Permission.EXPORT, surface="package_export"
    )


def require_package_approve(request: Request, db: DbSession, ctx: Tenant) -> PackageAccess:
    """Approve-class package actions: APPROVE, and never the package's own maker.

    The stored binding is consulted first (``authority="chain"``) and the scalar
    ladder remains behind it, so an Org Owner's Approver grant now actually
    approves — on a BSD or liquidity return as much as on an ICAAP — while no
    existing approver loses the route. This and
    :func:`require_package_stage_decision` take the SAME path on purpose: they
    write the same chain decision, and two authorities for one act is the seam
    this codebase keeps losing gates at (D-069).
    """
    return _require_package_access(
        request,
        db,
        ctx,
        gate="approver",
        permission=Permission.APPROVE,
        surface="package_approve",
        conditions=_package_maker_checker_conditions(request, db, ctx),
        authority="chain",
    )


def require_package_submit(request: Request, db: DbSession, ctx: Tenant) -> PackageAccess:
    """Transmission to the regulator: ``SUBMIT``, held by the Validator alone.

    This dependency used to require ``Permission.APPROVE`` — the same permission
    as :func:`require_package_approve` — so one authority both approved a return
    and filed it to the Bank of Ghana, and on an ungated family the scalar
    ``approver`` role was enough on its own. The bank's process has a separate
    Validator who is the only officer that transmits
    (``docs/filing_workflow_redesign.md`` §1 finding 3, §6 step 1).

    Every return family now takes the same scoped path: an interactive human,
    then one complete active binding carrying ``SUBMIT`` over Regulatory
    Reporting / restricted for this exact institution. No scalar role satisfies
    it, and the officer who generated the return still cannot release it.
    """
    return _require_package_access(
        request,
        db,
        ctx,
        gate="scoped",
        permission=Permission.SUBMIT,
        surface="package_submit",
        conditions=_package_transmission_conditions(request, db, ctx),
        authority="transmission",
    )


def require_package_stage_decision(request: Request, db: DbSession, ctx: Tenant) -> PackageAccess:
    """Deciding a stage of the filing chain: the authority the STAGE names.

    This is what makes Preparer, Approver and Validator three AUTHORITIES
    rather than three labels. The permission comes from the stage the return is
    actually waiting at (``filing_workflow.chain.stage_permission``): the
    transmitting stage takes ``SUBMIT``, a review stage ``REVIEW``, an approval
    stage ``APPROVE``. The ``validator`` bundle carries ``submit`` and
    deliberately not ``approve``, and the approver bundle the reverse, so
    neither can take the other's stage — and the stage engine never has to know
    a role string.

    The separation of duties (whoever prepared this round cannot approve it,
    whoever approved cannot validate it) rides as a MAKER_CHECKER condition so
    the refusal lands in the binding trace. The service re-checks it under the
    row lock, because two officers can decide between the two.

    Both branches consult the STORED BINDING. The transmitting stage consults it
    alone (step 1's cutover); the approve/review stages consult it first and keep
    the scalar ladder behind it, so an Org Owner's Approver grant authorises the
    decision on every family while no existing approver loses the route.
    """
    from app.services.filing_workflow import chain as filing_chain  # noqa: PLC0415 - cycle

    _bank, package = _resolve_package_from_path(request, db, ctx)
    stage = None if package is None else filing_chain.stage_for_decision(db, ctx, package)
    permission = filing_chain.stage_permission(stage)
    conditions = filing_chain.stage_authority(db, ctx, package)
    if permission is Permission.SUBMIT:
        return _require_package_access(
            request,
            db,
            ctx,
            gate="scoped",
            permission=Permission.SUBMIT,
            surface="package_stage_transmit_decision",
            conditions=conditions,
            authority="transmission",
        )
    return _require_package_access(
        request,
        db,
        ctx,
        gate="approver",
        permission=permission,
        surface="package_stage_decision",
        conditions=conditions,
        authority="chain",
    )


def _package_transmission_conditions(
    request: Request, db: Session, ctx: TenantContext
) -> tuple[ConditionCheck, ...]:
    """Four eyes on the regulator's copy, for EVERY family.

    ``Permission.SUBMIT`` declares this condition required
    (``services/authorization._REQUIRED_RUNTIME_CONDITIONS``), so an evaluation
    that does not receive it denies. The officer who generated the return is not
    the officer who files it — on a BSD return as much as on an ICAAP, which is
    why this builder does not dispatch on the family the way the approve-side
    one does.
    """
    from app.core.authorization import ConditionKind  # noqa: PLC0415

    _bank, package = _resolve_package_from_path(request, db, ctx)
    distinct = (
        package is not None
        and ctx.actor_user_id is not None
        and package.generated_by != ctx.actor_user_id
    )
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=distinct,
            reason=(
                "the officer filing this return is not the officer who generated it"
                if distinct
                else "the officer who generated this return cannot also file it"
            ),
        ),
    )


def _package_maker_checker_conditions(
    request: Request, db: Session, ctx: TenantContext
) -> tuple[ConditionCheck, ...]:
    """Four eyes on an approval, for EVERY family: the officer who generated the
    return cannot release it.

    Recorded as an authorization CONDITION rather than buried in a service, so
    the refusal carries a trace an examiner can read. The services re-check it —
    two requests can race between the decision and the write.

    This used to dispatch on the family and return NOTHING for an ungated one.
    ``Permission.APPROVE`` declares ``MAKER_CHECKER`` required, so an empty tuple
    denies: the binding branch could therefore never open on a BSD, liquidity,
    capital or FX return, which is every return a bank actually files. Four eyes
    on an approval is four eyes whatever the return is — the same reasoning as
    ``_package_transmission_conditions``, which has never dispatched on family.
    """
    from app.core.authorization import ConditionKind  # noqa: PLC0415

    _bank, package = _resolve_package_from_path(request, db, ctx)
    if package is None or ctx.actor_user_id is None:
        return ()
    distinct = package.generated_by != ctx.actor_user_id
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=distinct,
            reason=(
                "the officer releasing this return is not the officer who generated it"
                if distinct
                else "the officer who generated this return cannot also release it"
            ),
        ),
    )


Tenant = Annotated[TenantContext, Depends(get_tenant_context)]
MutationTenant = Annotated[TenantContext, Depends(get_mutation_tenant_context)]
ScopedMutationTenant = Annotated[TenantContext, Depends(get_scoped_mutation_tenant_context)]
ApproverTenant = Annotated[TenantContext, Depends(get_approver_tenant_context)]
GrantAdminTenant = Annotated[TenantContext, Depends(require_grant_administration)]
LiquidityMonitoringResource = Annotated[
    LiquidityMonitoringAccess, Depends(require_liquidity_monitoring_view)
]
IntegrationPushResource = Annotated[IntegrationPushAccess, Depends(require_integration_push_ingest)]
CapitalAggregatedView = Annotated[
    InstitutionPermissionAccess, Depends(require_capital_aggregated_view)
]
CapitalConfidentialView = Annotated[
    InstitutionPermissionAccess, Depends(require_capital_confidential_view)
]
CapitalRestrictedView = Annotated[
    InstitutionPermissionAccess, Depends(require_capital_restricted_view)
]
CapitalRun = Annotated[InstitutionPermissionAccess, Depends(require_capital_run)]
IcaapView = Annotated[IcaapAccess, Depends(require_icaap_view)]
IcaapCreate = Annotated[IcaapAccess, Depends(require_icaap_create)]
IcaapEdit = Annotated[IcaapAccess, Depends(require_icaap_edit)]
IcaapExport = Annotated[IcaapAccess, Depends(require_icaap_export)]
IcaapAiDraft = Annotated[IcaapAccess, Depends(require_icaap_ai_draft)]
AiSettingsAdminTenant = Annotated[TenantContext, Depends(require_ai_settings_administration)]
IcaapPillar2Approve = Annotated[IcaapAccess, Depends(require_icaap_pillar2_approve)]
IcaapAddonApprove = Annotated[IcaapAccess, Depends(require_icaap_addon_approve)]
IcaapAuditReview = Annotated[IcaapAccess, Depends(require_icaap_audit_review)]
IcaapCapitalPlanPropose = Annotated[IcaapAccess, Depends(require_icaap_capital_plan_propose)]
IcaapStageDecide = Annotated[IcaapAccess, Depends(require_icaap_stage_decision)]
IcaapFreeze = Annotated[IcaapAccess, Depends(require_icaap_freeze)]
IcaapReview = Annotated[IcaapAccess, Depends(require_icaap_review)]
IcaapWorkflowApprove = Annotated[IcaapAccess, Depends(require_icaap_workflow_approve)]
IcaapDisclosureApprove = Annotated[IcaapAccess, Depends(require_icaap_disclosure_approve)]
FxAggregatedView = Annotated[InstitutionPermissionAccess, Depends(require_fx_aggregated_view)]
FxRun = Annotated[InstitutionPermissionAccess, Depends(require_fx_run)]
CapitalPlanWrite = Annotated[InstitutionPermissionAccess, Depends(require_capital_plan_write)]
CapitalPlanApproveAccess = Annotated[
    InstitutionPermissionAccess, Depends(require_capital_plan_approve)
]
IlaapRefreshAccess = Annotated[InstitutionPermissionAccess, Depends(require_ilaap_refresh)]
LiquidityAggregatedResource = Annotated[
    LiquidityMonitoringAccess, Depends(require_liquidity_aggregated_view)
]
LiquidityConfidentialResource = Annotated[
    LiquidityMonitoringAccess, Depends(require_liquidity_confidential_view)
]
PackageView = Annotated[PackageAccess, Depends(require_package_view)]
PackageValidate = Annotated[PackageAccess, Depends(require_package_validate)]
PackageEdit = Annotated[PackageAccess, Depends(require_package_edit)]
PackageExport = Annotated[PackageAccess, Depends(require_package_export)]
PackageApprove = Annotated[PackageAccess, Depends(require_package_approve)]
PackageSubmit = Annotated[PackageAccess, Depends(require_package_submit)]
PackageStageDecide = Annotated[PackageAccess, Depends(require_package_stage_decision)]
Storage = Annotated[ObjectStorage, Depends(get_object_storage)]


def require_module_access(module_key: str):  # noqa: ANN201 - returns a FastAPI dependency
    """Server-side module scoping (docs/sdi.md §14, SDI Phase B).

    Rejects a request to a module the tenant's institution type is not entitled
    to. The frontend ``ModuleGuard`` hides bank-only modules for an SDI, but
    hiding is not security — an SDI must not reach bank-only functionality by
    calling the API directly. The entitled set is the institution-type registry's
    ``default_modules`` (the same data the nav is scoped from). ``bank_id`` is
    read from the path; a non-bank-scoped route in a gated router is not blocked.
    """
    from app.services import institution_types  # noqa: PLC0415 - avoid import cycle

    def _dependency(
        request: Request,
        db: DbSession,
        ctx: Tenant,
        bank: TenantBank,
    ) -> None:
        if bank is None:
            return
        if module_key not in institution_types.get_type(db, bank).default_modules:
            authorization_denied(
                reason="module_not_entitled",
                module=module_key,
                organization_id=ctx.organization_id,
                bank_id=bank.id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"The '{module_key}' module is not available for this institution's "
                    "type. This functionality is scoped out for the institution class."
                ),
            )

    return Depends(_dependency)
