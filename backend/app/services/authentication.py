"""Authentication service: password login (with lockout), token issue + refresh.

Identity is established by verifying credentials here and minting signed app tokens
(:mod:`app.core.security`). The API layer then authenticates every request by verifying
the token — never by trusting a header. Failed logins are throttled per user
(``failed_login_attempts`` / ``locked_until``) to blunt brute force, and error responses
are deliberately generic so they never reveal whether an email exists.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import AuthSettings, get_settings
from app.db.base import utc_now
from app.models import User

_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password."
)


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int


def issue_tokens(user: User, settings: AuthSettings | None = None) -> IssuedTokens:
    settings = settings or get_settings().auth
    now = utc_now()
    common = {
        "subject": user.id,
        "organization_id": user.organization_id,
        "roles": [user.role],
        "email": user.email,
        "name": user.display_name,
        "now": now,
        "settings": settings,
    }
    return IssuedTokens(
        access_token=security.create_token(token_type="access", **common),
        refresh_token=security.create_token(token_type="refresh", **common),
        expires_in=settings.access_token_ttl_seconds,
    )


def _resolve_user(db: Session, email: str, organization_id: UUID | None) -> User | None:
    stmt = select(User).where(User.email == email, User.is_active.is_(True))
    if organization_id is not None:
        stmt = stmt.where(User.organization_id == organization_id)
    users = db.scalars(stmt).all()
    # Exactly-one match logs in; an email shared across orgs must be disambiguated.
    return users[0] if len(users) == 1 else None


def login_with_password(
    db: Session,
    *,
    email: str,
    password: str,
    organization_id: UUID | None = None,
    settings: AuthSettings | None = None,
) -> IssuedTokens:
    settings = settings or get_settings().auth
    now = utc_now()
    user = _resolve_user(db, email, organization_id)

    # Uniform failure for unknown user / no password / SSO-only account: never disclose which.
    if user is None or user.auth_provider != "password" or not user.password_hash:
        raise _INVALID

    if user.locked_until is not None and _as_aware(user.locked_until) > now:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account temporarily locked after repeated failures. Try again later.",
        )

    if not security.verify_password(password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.max_failed_logins:
            user.locked_until = now + dt.timedelta(seconds=settings.lockout_seconds)
        db.commit()
        raise _INVALID

    # Success: reset throttle, stamp login, opportunistically upgrade the hash.
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
    db.commit()
    return issue_tokens(user, settings)


def _resolve_sso_user(
    db: Session, subject: str, email: str | None, organization_id: UUID | None
) -> User | None:
    # Already linked to this Auth0 subject?
    linked_stmt = select(User).where(
        User.auth_provider == "auth0",
        User.sso_subject == subject,
        User.is_active.is_(True),
    )
    if organization_id is not None:
        linked_stmt = linked_stmt.where(User.organization_id == organization_id)
    linked = db.scalars(linked_stmt).all()
    if len(linked) == 1:
        return linked[0]
    if len(linked) > 1:
        return None
    # First SSO login: match a pre-provisioned account by email (no auto-provisioning —
    # an unknown identity is rejected, so only invited users get in).
    if not email:
        return None
    email_stmt = select(User).where(User.email == email, User.is_active.is_(True))
    if organization_id is not None:
        email_stmt = email_stmt.where(User.organization_id == organization_id)
    matches = db.scalars(email_stmt).all()
    return matches[0] if len(matches) == 1 else None


def login_with_sso(
    db: Session,
    *,
    id_token: str,
    organization_id: UUID | None = None,
    settings: AuthSettings | None = None,
) -> IssuedTokens:
    """Verify an Auth0 id_token, link it to a pre-provisioned user, issue app tokens."""
    settings = settings or get_settings().auth
    try:
        claims = security.verify_auth0_id_token(id_token, settings=settings)
    except security.AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid SSO token."
        ) from exc

    user = _resolve_sso_user(db, str(claims["sub"]), claims.get("email"), organization_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No AequorOS account is provisioned for this identity.",
        )
    user.auth_provider = "auth0"
    user.sso_subject = str(claims["sub"])
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = utc_now()
    db.commit()
    return issue_tokens(user, settings)


def refresh_tokens(
    db: Session, *, refresh_token: str, settings: AuthSettings | None = None
) -> IssuedTokens:
    settings = settings or get_settings().auth
    try:
        claims = security.decode_token(
            refresh_token, expected_type="refresh", settings=settings
        )
    except security.AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token."
        ) from exc
    user = db.scalar(
        select(User).where(
            User.id == UUID(claims["sub"]),
            User.organization_id == UUID(claims["org"]),
            User.is_active.is_(True),
        )
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token."
        )
    return issue_tokens(user, settings)


def set_password(db: Session, user: User, password: str, *, commit: bool = True) -> None:
    """Set (or reset) a user's password and clear any lockout."""
    user.password_hash = security.hash_password(password)
    user.auth_provider = "password"
    user.failed_login_attempts = 0
    user.locked_until = None
    if commit:
        db.commit()


def _as_aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
