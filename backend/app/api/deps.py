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
    # Set ONLY under operator act-as-examiner impersonation: the originating
    # inspector session id. Its presence marks the principal as a read-only
    # operator view (actor_user_id is None — the actor is staff, not a tenant
    # user). RLS still pins to ``organization_id``, so a single-tenant view.
    impersonation_context: str | None = None
    # The email of the operator acting as examiner (impersonation only) —
    # provenance for audit; never a tenant identity.
    actor_operator: str | None = None


def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> TenantContext:
    """Authenticate a request by verifying its bearer access token (zero-trust).

    The tenant + user + roles come from the *verified* token claims, never from a
    header a caller can spoof. This is the auth boundary the API depends on.
    """
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
    )


def require_role(minimum: str):  # noqa: ANN201 - returns a FastAPI dependency callable
    """Dependency factory: 403 unless the caller holds ``minimum`` (or higher)."""

    def _dependency(
        ctx: Annotated[TenantContext, Depends(get_current_principal)],
    ) -> TenantContext:
        if not security.has_role(list(ctx.roles), minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the '{minimum}' role or higher.",
            )
        return ctx

    return _dependency


def get_tenant_context(
    principal: Annotated[TenantContext, Depends(get_current_principal)],
) -> TenantContext:
    """Tenant context for a request — derived from the verified bearer token.

    (Was demo header-trust; now every request is authenticated by JWT signature.)
    """
    return principal


def get_mutation_tenant_context(
    principal: Annotated[TenantContext, Depends(get_current_principal)],
) -> TenantContext:
    """Tenant context for a mutating request: requires an acting user AND the
    ``analyst`` role (or higher). This single gate makes ``viewer`` accounts strictly
    read-only across every mutation endpoint (RBAC — the write side of the model)."""
    # Act-as-examiner impersonation is strictly read-only: an operator viewing a
    # tenant may NEVER mutate its state. Refuse here — before the actor/role
    # checks — so the reason is explicit and unmistakable. Defense in depth: the
    # role is also pinned to ``examiner``, which sits in no mutation ladder.
    if principal.impersonation_context is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Impersonation sessions are read-only; this action is not permitted.",
        )
    if principal.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    if not security.has_role(list(principal.roles), "analyst"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the 'analyst' role or higher.",
        )
    return principal


def get_tenant_db_session(
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
) -> Iterator[Session]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ctx.organization_id
    try:
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

    actor_user_id = session.scalar(
        select(User.id).where(
            User.id == ctx.actor_user_id,
            User.organization_id == ctx.organization_id,
            User.is_active.is_(True),
        )
    )
    if actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context is not valid.",
        )


DbSession = Annotated[Session, Depends(get_tenant_db_session)]


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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the 'approver' role or higher.",
        )
    return principal


Tenant = Annotated[TenantContext, Depends(get_tenant_context)]
MutationTenant = Annotated[TenantContext, Depends(get_mutation_tenant_context)]
ApproverTenant = Annotated[TenantContext, Depends(get_approver_tenant_context)]
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
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
        if module_key not in institution_types.get_type(db, bank).default_modules:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"The '{module_key}' module is not available for this institution's "
                    "type. This functionality is scoped out for the institution class."
                ),
            )

    return Depends(_dependency)
