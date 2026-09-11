from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.core.observability import authorization_denied, cross_tenant_attempt
from app.db.session import get_sessionmaker
from app.integrations.storage.base import ObjectStorage
from app.integrations.storage.s3 import get_object_storage
from app.models import Bank, Organization, User

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
        "require_grant_administration",
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


def _require_organization_account_permission(
    db: Session,
    ctx: TenantContext,
    *,
    permission: str,
    surface: str,
    detail: str,
) -> TenantContext:
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

    required_permission = Permission(permission)
    resource = ResourceLocator(
        ctx.organization_id,
        InstitutionScope.ORGANIZATION,
        None,
        Module.ACCOUNT,
        Sensitivity.RESTRICTED,
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

    from app.services import authorization as authorization_service  # noqa: PLC0415
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
            module="liq",
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    if ctx.actor_user_id is None or ctx.authorization_version is None:
        authorization_denied(
            reason="liquidity_monitoring_human_binding_required",
            organization_id=ctx.organization_id,
            bank_id=bank.id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Liquidity Monitoring access requires an active scoped binding.",
        )

    try:
        decision = authorization_service.evaluate_liquidity_monitoring_view(
            db,
            organization_id=ctx.organization_id,
            principal_id=ctx.actor_user_id,
            institution=bank,
            surface="liquidity_monitoring",
        )
    except Exception as exc:  # noqa: BLE001 - enforcement must deny on evaluator failure
        authorization_denied(
            reason="binding_evaluation_failed",
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
            bank_id=bank.id,
            module="liq",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Liquidity Monitoring access requires an active scoped binding.",
        ) from exc

    if not decision.allowed:
        authorization_denied(
            reason=decision.reason,
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
            bank_id=bank.id,
            module="liq",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Liquidity Monitoring access requires an active scoped binding.",
        )
    return LiquidityMonitoringAccess(ctx=ctx, bank=bank)


def require_integration_push_ingest(
    request: Request,
    db: DbSession,
    ctx: Tenant,
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
    # held through the route's service commit, so a key cannot be revoked after
    # authorization but before its storage/database mutation completes.
    integration_keys.lock_authenticated_key(db, ctx)
    bank = db.scalar(
        select(Bank).where(
            Bank.id == requested_bank_id,
            Bank.organization_id == ctx.organization_id,
        )
    )
    if bank is None:
        cross_tenant_attempt(
            reason="bank_not_visible_to_integration_key",
            organization_id=ctx.organization_id,
            bank_id=requested_bank_id,
        )
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


Tenant = Annotated[TenantContext, Depends(get_tenant_context)]
MutationTenant = Annotated[TenantContext, Depends(get_mutation_tenant_context)]
ApproverTenant = Annotated[TenantContext, Depends(get_approver_tenant_context)]
GrantAdminTenant = Annotated[TenantContext, Depends(require_grant_administration)]
LiquidityMonitoringResource = Annotated[
    LiquidityMonitoringAccess, Depends(require_liquidity_monitoring_view)
]
IntegrationPushResource = Annotated[IntegrationPushAccess, Depends(require_integration_push_ingest)]
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

    def _dependency(request: Request, db: DbSession, ctx: Tenant) -> None:
        bank_id = request.path_params.get("bank_id")
        if not bank_id:
            return
        bank = db.scalar(
            select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
        )
        if bank is None:
            # RLS makes a genuine miss and a cross-tenant probe identical here:
            # both return no row. Reported with a reason code that says exactly
            # what was observed rather than asserting intent.
            cross_tenant_attempt(
                reason="bank_not_visible_to_tenant",
                organization_id=ctx.organization_id,
                bank_id=str(bank_id),
                module=module_key,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
        if module_key not in institution_types.get_type(db, bank).default_modules:
            authorization_denied(
                reason="module_not_entitled",
                module=module_key,
                organization_id=ctx.organization_id,
                bank_id=str(bank_id),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"The '{module_key}' module is not available for this institution's "
                    "type. This functionality is scoped out for the institution class."
                ),
            )

    return Depends(_dependency)
