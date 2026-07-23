"""Authentication routes: login, refresh, current user.

``users``/``organizations`` are RLS-forced, so a login (which has only an email, no
tenant context yet) resolves the user through the cross-tenant *system* session (the
BYPASSRLS worker role). Every authenticated request thereafter carries a verified
token whose ``org`` claim scopes an ordinary tenant session.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterator
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    TenantContext,
    get_current_principal,
    get_tenant_db_session,
    require_role,
)
from app.core.config import get_settings
from app.db.session import get_worker_sessionmaker
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    ProfileUpdateRequest,
    SsoAccessRequestApprove,
    SsoAccessRequestRead,
    SsoClientConfigResponse,
    SsoConnectionResponse,
    SsoConnectionUpdateRequest,
    SsoLoginRequest,
    SsoStatusResponse,
    TokenRefreshRequest,
    TokenResponse,
)
from app.services import authentication, sso_config

router = APIRouter(prefix="/auth", tags=["auth"])


def _system_session() -> Iterator[Session]:
    """Cross-tenant session for auth lookups (BYPASSRLS worker role)."""
    session = get_worker_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


SystemDb = Annotated[Session, Depends(_system_session)]


def _tokens(issued: authentication.IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
    )


def _me_response(user: User) -> MeResponse:
    return MeResponse(
        user_id=user.id,
        organization_id=user.organization_id,
        email=user.email,
        display_name=user.display_name,
        job_title=user.job_title,
        locale=user.locale,
        timezone=user.timezone,
        # The database CHECK constraint and update schema guarantee this set;
        # SQLAlchemy exposes String columns as the wider `str` type.
        theme=cast(Literal["light", "dark", "system"] | None, user.theme),
        role=user.role,
    )


def _current_user(db: Session, ctx: TenantContext) -> User:
    user = db.scalar(
        select(User).where(
            User.id == ctx.actor_user_id,
            User.organization_id == ctx.organization_id,
        )
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    return user


@router.post("/login", response_model=TokenResponse, operation_id="authLogin")
def login(payload: LoginRequest, db: SystemDb) -> TokenResponse:
    return _tokens(
        authentication.login_with_password(
            db,
            email=payload.email,
            password=payload.password,
            organization_id=payload.organization_id,
        )
    )


@router.post("/sso", response_model=TokenResponse, operation_id="authSso")
def sso_login(payload: SsoLoginRequest, db: SystemDb) -> TokenResponse:
    """Exchange a verified OIDC id_token for AequorOS app tokens."""
    return _tokens(
        authentication.login_with_sso(
            db, id_token=payload.id_token, organization_id=payload.organization_id
        )
    )


@router.get("/sso/status", response_model=SsoStatusResponse, operation_id="authSsoStatus")
def sso_status(db: SystemDb) -> SsoStatusResponse:
    """Public probe for the login page: is SSO sign-in available?

    Runs on the system session because it is called before any tenant context
    exists; it discloses only a boolean.
    """
    try:
        config = sso_config.resolve_client_config(db)
    except HTTPException:
        # >1 enabled connection is a server misconfiguration; the login page
        # just hides the button rather than erroring.
        return SsoStatusResponse(enabled=False)
    return SsoStatusResponse(enabled=config is not None)


@router.get(
    "/sso/connection",
    response_model=SsoConnectionResponse | None,
    operation_id="authGetSsoConnection",
)
def get_sso_connection(
    ctx: Annotated[TenantContext, Depends(require_role("admin"))],
    db: Annotated[Session, Depends(get_tenant_db_session)],
) -> SsoConnectionResponse | None:
    """The org's OIDC connection (admin). The secret is never returned — only
    whether one is set."""
    connection = sso_config.get_connection(db, ctx.organization_id)
    if connection is None:
        return None
    return SsoConnectionResponse(
        issuer=connection.issuer,
        client_id=connection.client_id,
        client_secret_set=bool(connection.client_secret_ciphertext),
        allowed_email_domains=list(connection.allowed_email_domains),
        enabled=connection.enabled,
        jit_enabled=connection.jit_enabled,
    )


@router.put(
    "/sso/connection",
    response_model=SsoConnectionResponse,
    operation_id="authPutSsoConnection",
)
def put_sso_connection(
    payload: SsoConnectionUpdateRequest,
    ctx: Annotated[TenantContext, Depends(require_role("admin"))],
    db: Annotated[Session, Depends(get_tenant_db_session)],
) -> SsoConnectionResponse:
    """Create or update the org's OIDC connection (admin; secret write-only)."""
    connection = sso_config.upsert_connection(
        db,
        organization_id=ctx.organization_id,
        issuer=payload.issuer,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
        allowed_email_domains=payload.allowed_email_domains,
        enabled=payload.enabled,
        jit_enabled=payload.jit_enabled,
        actor_user_id=ctx.actor_user_id,
    )
    return SsoConnectionResponse(
        issuer=connection.issuer,
        client_id=connection.client_id,
        client_secret_set=bool(connection.client_secret_ciphertext),
        allowed_email_domains=list(connection.allowed_email_domains),
        enabled=connection.enabled,
        jit_enabled=connection.jit_enabled,
    )


@router.get(
    "/sso/access-requests",
    response_model=list[SsoAccessRequestRead],
    operation_id="authListSsoAccessRequests",
)
def list_sso_access_requests(
    ctx: Annotated[TenantContext, Depends(require_role("admin"))],
    db: Annotated[Session, Depends(get_tenant_db_session)],
) -> list[SsoAccessRequestRead]:
    """JIT sign-ins awaiting approval (deactivated stubs; admin only)."""
    return [
        SsoAccessRequestRead(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            requested_at=user.created_at,
        )
        for user in authentication.list_sso_access_requests(db, ctx.organization_id)
    ]


@router.post(
    "/sso/access-requests/{user_id}/approve",
    response_model=SsoAccessRequestRead,
    operation_id="authApproveSsoAccessRequest",
)
def approve_sso_access_request(
    user_id: UUID,
    payload: SsoAccessRequestApprove,
    ctx: Annotated[TenantContext, Depends(require_role("admin"))],
    db: Annotated[Session, Depends(get_tenant_db_session)],
) -> SsoAccessRequestRead:
    """Activate a requested account with an explicitly chosen role (admin only)."""
    user = authentication.approve_sso_access_request(
        db, organization_id=ctx.organization_id, user_id=user_id, role=payload.role
    )
    return SsoAccessRequestRead(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        requested_at=user.created_at,
    )


@router.post(
    "/sso/access-requests/{user_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="authRejectSsoAccessRequest",
)
def reject_sso_access_request(
    user_id: UUID,
    ctx: Annotated[TenantContext, Depends(require_role("admin"))],
    db: Annotated[Session, Depends(get_tenant_db_session)],
) -> None:
    """Delete a never-activated request stub (admin only)."""
    authentication.reject_sso_access_request(
        db, organization_id=ctx.organization_id, user_id=user_id
    )


@router.get("/sso/client-config", include_in_schema=False)
def sso_client_config(
    db: SystemDb,
    x_internal_auth: Annotated[str | None, Header(alias="X-Internal-Auth")] = None,
) -> SsoClientConfigResponse:
    """Server-to-server only: the dashboard's NextAuth fetches the full OIDC
    client config (secret included) to run the sign-in flow. Gated by
    ``SSO_INTERNAL_KEY`` — the one plaintext read path for the client secret;
    excluded from the OpenAPI schema and never called from a browser.
    """
    internal_key = get_settings().auth.sso_internal_key
    if not internal_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not configured.")
    if not x_internal_auth or not hmac.compare_digest(x_internal_auth, internal_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key."
        )
    config = sso_config.resolve_client_config(db)
    if config is None:
        return SsoClientConfigResponse(enabled=False)
    return SsoClientConfigResponse(
        enabled=True,
        issuer=config.issuer,
        client_id=config.client_id,
        client_secret=config.client_secret,
    )


@router.post("/refresh", response_model=TokenResponse, operation_id="authRefresh")
def refresh(payload: TokenRefreshRequest, db: SystemDb) -> TokenResponse:
    return _tokens(authentication.refresh_tokens(db, refresh_token=payload.refresh_token))


@router.get("/me", response_model=MeResponse, operation_id="authMe")
def me(
    ctx: Annotated[TenantContext, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_tenant_db_session)],
) -> MeResponse:
    return _me_response(_current_user(db, ctx))


@router.patch("/me", response_model=MeResponse, operation_id="authUpdateMe")
def update_me(
    payload: ProfileUpdateRequest,
    ctx: Annotated[TenantContext, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_tenant_db_session)],
) -> MeResponse:
    user = _current_user(db, ctx)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return _me_response(user)
