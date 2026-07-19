"""Authentication routes: login, refresh, current user.

``users``/``organizations`` are RLS-forced, so a login (which has only an email, no
tenant context yet) resolves the user through the cross-tenant *system* session (the
BYPASSRLS worker role). Every authenticated request thereafter carries a verified
token whose ``org`` claim scopes an ordinary tenant session.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext, get_current_principal
from app.db.session import get_sessionmaker, get_worker_sessionmaker
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    SsoLoginRequest,
    TokenRefreshRequest,
    TokenResponse,
)
from app.services import authentication

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
    """Exchange a verified Auth0 id_token for AequorOS app tokens."""
    return _tokens(
        authentication.login_with_sso(
            db, id_token=payload.id_token, organization_id=payload.organization_id
        )
    )


@router.post("/refresh", response_model=TokenResponse, operation_id="authRefresh")
def refresh(payload: TokenRefreshRequest, db: SystemDb) -> TokenResponse:
    return _tokens(authentication.refresh_tokens(db, refresh_token=payload.refresh_token))


@router.get("/me", response_model=MeResponse, operation_id="authMe")
def me(ctx: Annotated[TenantContext, Depends(get_current_principal)]) -> MeResponse:
    session = get_sessionmaker()()
    session.info["organization_id"] = ctx.organization_id
    try:
        user = session.scalar(
            select(User).where(
                User.id == ctx.actor_user_id,
                User.organization_id == ctx.organization_id,
            )
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found."
            )
        return MeResponse(
            user_id=user.id,
            organization_id=user.organization_id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
        )
    finally:
        session.close()
