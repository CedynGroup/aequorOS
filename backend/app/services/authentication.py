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
from app.models import SsoConnection, User
from app.services import sso_config

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
    # Already linked to this OIDC subject?
    linked_stmt = select(User).where(
        User.auth_provider == "oidc",
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


_SSO_INVALID = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid SSO token.")


_SSO_PENDING = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail=(
        "Your access request has been recorded. An administrator must approve "
        "your account before you can sign in."
    ),
)


def _inactive_user_by_email(db: Session, organization_id: UUID, email: str) -> User | None:
    return db.scalar(
        select(User).where(
            User.organization_id == organization_id,
            User.email == email,
            User.is_active.is_(False),
        )
    )


def _record_sso_access_request(db: Session, *, connection: SsoConnection, claims: dict) -> None:
    """JIT is a REQUEST, never access: the account is created deactivated and no
    tokens are issued — an admin must approve it (with a role) before the first
    real sign-in. Guarded twice: the connection must opt in AND carry a non-empty
    domain allow-list (re-checked here so a hand-edited row can never open
    public sign-up)."""
    db.add(
        User(
            organization_id=connection.organization_id,
            email=str(claims["email"]),
            display_name=str(claims["name"]) if claims.get("name") else None,
            role="viewer",
            auth_provider="oidc",
            sso_subject=str(claims["sub"]),
            is_active=False,
        )
    )
    db.commit()


def login_with_sso(
    db: Session,
    *,
    id_token: str,
    organization_id: UUID | None = None,
    settings: AuthSettings | None = None,
) -> IssuedTokens:
    """Verify an OIDC id_token against its configured connection, link it to a
    pre-provisioned user, and issue app tokens.

    Routing is zero-trust: the token's unverified ``iss``/``aud`` only *select* a
    stored, enabled connection — verification then runs against that connection's
    issuer JWKS and client id, so a forged header buys nothing.
    """
    settings = settings or get_settings().auth
    try:
        hints = security.unverified_claims(id_token)
    except security.AuthError as exc:
        raise _SSO_INVALID from exc

    audience_hint = hints.get("aud", "")
    if isinstance(audience_hint, list):  # OIDC allows a list; ours is a single RP
        audience_hint = audience_hint[0] if audience_hint else ""
    connection = sso_config.find_enabled_by_issuer_audience(
        db, issuer=str(hints.get("iss", "")), audience=str(audience_hint)
    )
    if connection is None:
        raise _SSO_INVALID

    try:
        claims = security.verify_oidc_id_token(
            id_token, issuer=connection.issuer, audience=connection.client_id
        )
    except security.AuthError as exc:
        raise _SSO_INVALID from exc

    email = claims.get("email")
    # An unverified email must never link to an account (Google always sends the
    # flag; IdPs that omit it — e.g. Entra — pass, and the pre-provisioning gate
    # below still applies).
    if email is not None and claims.get("email_verified") is False:
        raise _SSO_INVALID
    if connection.allowed_email_domains:
        domain = str(email or "").rsplit("@", 1)[-1].lower()
        if domain not in connection.allowed_email_domains:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This email domain is not allowed for SSO sign-in.",
            )

    resolved_org = organization_id if organization_id is not None else connection.organization_id
    user = _resolve_sso_user(db, str(claims["sub"]), email, resolved_org)
    if (
        user is None
        and connection.jit_enabled
        and connection.allowed_email_domains
        and email
        and resolved_org == connection.organization_id
    ):
        # Access-request flow: record (or re-acknowledge) a deactivated stub and
        # refuse the session — approval is an explicit admin act.
        if _inactive_user_by_email(db, connection.organization_id, str(email)) is None:
            _record_sso_access_request(db, connection=connection, claims=claims)
        raise _SSO_PENDING
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No AequorOS account is provisioned for this identity.",
        )
    user.auth_provider = "oidc"
    user.sso_subject = str(claims["sub"])
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = utc_now()
    db.commit()
    return issue_tokens(user, settings)


# -- SSO access requests (JIT stubs awaiting admin approval) -------------------
def _access_request_stmt(organization_id: UUID):  # noqa: ANN202 - sqlalchemy Select
    """A pure JIT stub: deactivated, OIDC-linked, never logged in, no password.
    Deliberately narrow so admin-deactivated (offboarded) accounts never show
    up as approvable requests."""
    return select(User).where(
        User.organization_id == organization_id,
        User.is_active.is_(False),
        User.auth_provider == "oidc",
        User.password_hash.is_(None),
        User.last_login_at.is_(None),
    )


def list_sso_access_requests(db: Session, organization_id: UUID) -> list[User]:
    return list(db.scalars(_access_request_stmt(organization_id).order_by(User.created_at)))


def _get_access_request(db: Session, organization_id: UUID, user_id: UUID) -> User:
    user = db.scalar(_access_request_stmt(organization_id).where(User.id == user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Access request not found."
        )
    return user


def approve_sso_access_request(
    db: Session, *, organization_id: UUID, user_id: UUID, role: str
) -> User:
    """The authorization act: an admin activates the requested account with an
    explicitly chosen role."""
    user = _get_access_request(db, organization_id, user_id)
    user.role = role
    user.is_active = True
    db.commit()
    db.refresh(user)
    return user


def reject_sso_access_request(db: Session, *, organization_id: UUID, user_id: UUID) -> None:
    """Deletes the never-activated stub (safe: it has no history); the employee
    can request again, which recreates it."""
    user = _get_access_request(db, organization_id, user_id)
    db.delete(user)
    db.commit()


def refresh_tokens(
    db: Session, *, refresh_token: str, settings: AuthSettings | None = None
) -> IssuedTokens:
    settings = settings or get_settings().auth
    try:
        claims = security.decode_token(refresh_token, expected_type="refresh", settings=settings)
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
