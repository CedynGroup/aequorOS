from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.db.session import get_sessionmaker
from app.integrations.storage.base import ObjectStorage
from app.integrations.storage.s3 import get_object_storage
from app.models import Organization, User

# Declares a `bearerAuth` (HTTP bearer) security scheme in OpenAPI; auto_error=False so
# we raise our own 401 (with WWW-Authenticate) instead of FastAPI's default 403.
_bearer_scheme = HTTPBearer(auto_error=False, description="App JWT access token")


@dataclass(frozen=True)
class TenantContext:
    organization_id: UUID
    actor_user_id: UUID | None = None
    roles: tuple[str, ...] = ()


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
        organization_id=UUID(claims["org"]),
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
Tenant = Annotated[TenantContext, Depends(get_tenant_context)]
MutationTenant = Annotated[TenantContext, Depends(get_mutation_tenant_context)]
Storage = Annotated[ObjectStorage, Depends(get_object_storage)]
