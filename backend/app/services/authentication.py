"""Authentication service: password login (with lockout), token issue + refresh.

Identity is established by verifying credentials here and minting signed app tokens
(:mod:`app.core.security`). The API layer then authenticates every request by verifying
the token — never by trusting a header. Failed logins are throttled per user
(``failed_login_attempts`` / ``locked_until``) to blunt brute force, and error responses
are deliberately generic so they never reveal whether an email exists.

**Refresh-token lifecycle (audit finding P0-5).** Access tokens stay stateless and
short-lived; refresh tokens do not. Each one carries a ``jti`` and has a row in
``refresh_tokens`` holding a SHA-256 digest of the token (never the token), so a
session can be ended before its 14-day expiry. Refreshing ROTATES: the presented
token is retired and a new one issued into the same family. Presenting a retired
token outside ``AuthSettings.refresh_rotation_grace_seconds`` is treated as theft
and revokes the whole family. Logout, ``set_password`` and deactivation revoke
outright. A refresh token with no ``jti`` — anything minted before migration
``202608220028`` — is refused. Every app token also carries the user's
``authorization_version``; tokens predating ``202608250044`` and tokens whose
version is stale are refused, so affected sessions re-authenticate once.

**Concurrency (audit finding D-30).** Every write to ``refresh_tokens`` — the
INSERT in :func:`issue_tokens` and the revoking UPDATE in :func:`_revoke_where` —
first takes a row lock on the owning ``users`` row (:func:`_lock_session_owner`).
That is the whole guarantee, and it is a database one: see that function's
docstring for the phantom it closes and why a constraint cannot express it.
Locks are always taken ``users`` → ``refresh_tokens``, never the reverse, so the
two paths serialize instead of deadlocking.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core import security
from app.core.config import AuthSettings, get_settings
from app.db.base import utc_now
from app.models import RefreshToken, SsoConnection, User
from app.services import auth_throttle, sso_config

_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password."
)

# One generic answer for every refresh failure — expired, unknown, revoked,
# rotated, wrong bytes. The client cannot tell "your session was killed for
# reuse" from "that token never existed", which is exactly what we want.
_INVALID_REFRESH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token."
)


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int


def _hash_refresh_token(token: str) -> str:
    """The stored form of a refresh token. Only the digest is persisted, so a
    database reader (or a leaked backup) holds nothing presentable."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _lock_session_owner(db: Session, user_id: UUID) -> None:
    """Serialize every session mutation for one user on that user's own row.

    **The defect this closes (audit finding D-30).** Rotation INSERTs a successor
    while holding ``FOR UPDATE`` on its ancestor; revocation is a single
    ``UPDATE refresh_tokens … WHERE user_id = … AND revoked_at IS NULL``. Under
    READ COMMITTED the revoking statement fixes its snapshot when it starts, then
    blocks on the ancestor the rotation holds. When the rotation commits, the
    revoker wakes, re-checks the row it was blocked on — and never rescans, so
    the successor born after its snapshot is invisible to it. It revokes the
    parent and leaves the child live. A password reset or a deactivation racing
    an in-flight refresh therefore ended with a fully valid, 14-day refresh token
    in the attacker's hands: exactly the outcome P0-5 exists to prevent.

    **Why a lock and not a constraint.** There is nothing to conflict on. The
    successor is a legitimate row with a unique digest; what is wrong is only
    that it exists *after* a revocation it never saw. A ``UNIQUE`` index cannot
    express that, and the obvious candidate — one live token per family — is
    false by design: the rotation grace window deliberately issues siblings into
    the same family (see :func:`refresh_tokens`). SERIALIZABLE would detect it,
    but at the cost of putting every login on the platform behind
    serialization-failure retries. A row lock on the one object both paths
    already care about is the narrow form of the same guarantee.

    ``FOR NO KEY UPDATE``, not ``FOR UPDATE``: it conflicts with itself (which is
    the mutual exclusion wanted, and is also the strength Postgres takes for the
    plain ``UPDATE users`` that ``set_password`` and ``deactivate_user`` emit),
    while leaving ``FOR KEY SHARE`` — the lock any *other* table's foreign key
    into ``users`` takes — unblocked.

    On SQLite the clause is dropped by the dialect and the database's own
    write lock serializes whole transactions, so the ordering guarantee holds
    there too (the same argument :func:`_lock_refresh_token` records).
    """
    db.execute(select(User.id).where(User.id == user_id).with_for_update(key_share=True))


def issue_tokens(
    db: Session,
    user: User,
    settings: AuthSettings | None = None,
    *,
    family_id: UUID | None = None,
    rotated_from: RefreshToken | None = None,
) -> IssuedTokens:
    """Mint an access/refresh pair and record the refresh token's state.

    ``family_id`` continues an existing session lineage (a rotation); omitted, the
    new token starts its own family — that is what a fresh login is.
    """
    settings = settings or get_settings().auth
    # Before the row exists, not after: a revocation that has already begun must
    # either finish before this successor is written (and this call then finds a
    # revoked ancestor) or wait and see it. See _lock_session_owner (D-30).
    _lock_session_owner(db, user.id)
    db.refresh(user)
    now = utc_now()
    token_id = uuid4()
    common = {
        "subject": user.id,
        "organization_id": user.organization_id,
        "roles": [user.role],
        "authorization_version": user.authorization_version,
        "email": user.email,
        "name": user.display_name,
        "now": now,
        "settings": settings,
    }
    access_token = security.create_token(token_type="access", **common)
    refresh_token = security.create_token(token_type="refresh", jti=str(token_id), **common)
    db.add(
        RefreshToken(
            id=token_id,
            organization_id=user.organization_id,
            user_id=user.id,
            family_id=family_id or token_id,
            token_hash=_hash_refresh_token(refresh_token),
            issued_at=now,
            expires_at=now + dt.timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    if rotated_from is not None:
        # Stamped ONCE. Leaving the original instant in place is what keeps the
        # grace window from sliding: a token re-presented again and again cannot
        # push its own deadline forward.
        if rotated_from.rotated_at is None:
            rotated_from.rotated_at = now
        if rotated_from.replaced_by_id is None:
            rotated_from.replaced_by_id = token_id
    db.commit()
    return IssuedTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_ttl_seconds,
    )


# -- revocation ---------------------------------------------------------------
def _revoke_where(
    db: Session,
    criterion: ColumnElement[bool],
    *,
    owner_id: UUID,
    reason: str,
    commit: bool,
) -> int:
    """Mark every still-live token matching ``criterion`` revoked; row count out.

    ``owner_id`` is the user whose sessions these are, and it is not redundant
    with ``criterion``: it names the row to lock BEFORE the UPDATE, which is what
    stops a concurrent rotation from slipping a successor past this statement's
    snapshot (:func:`_lock_session_owner`, audit finding D-30). A family belongs
    to exactly one user — ``family_id`` is inherited from the login that started
    it — so one lock covers either criterion.

    ``synchronize_session=False`` because the caller either raises straight after
    (the refresh path) or commits (password change, deactivation) — no identity-
    mapped ``RefreshToken`` is read again in this session.
    """
    _lock_session_owner(db, owner_id)
    result = cast(
        "CursorResult[Any]",
        db.execute(
            update(RefreshToken)
            .where(criterion, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utc_now(), revoked_reason=reason)
            .execution_options(synchronize_session=False),
        ),
    )
    if commit:
        db.commit()
    return int(result.rowcount or 0)


def revoke_refresh_family(
    db: Session, family_id: UUID, *, owner_id: UUID, reason: str, commit: bool = True
) -> int:
    """Revoke every live token in one session lineage. Returns the row count."""
    return _revoke_where(
        db,
        RefreshToken.family_id == family_id,
        owner_id=owner_id,
        reason=reason,
        commit=commit,
    )


def revoke_user_refresh_tokens(
    db: Session, user_id: UUID, *, reason: str, commit: bool = True
) -> int:
    """Revoke every live refresh token a user holds — all devices, all sessions."""
    return _revoke_where(
        db,
        RefreshToken.user_id == user_id,
        owner_id=user_id,
        reason=reason,
        commit=commit,
    )


def deactivate_user(db: Session, user: User, *, commit: bool = True) -> None:
    """Deactivate an account AND end its sessions.

    ``is_active = False`` alone already blocks the next refresh, but leaving the
    rows live means the kill is only ever discovered at the next refresh attempt;
    revoking makes the state on disk say what happened.
    """
    user.is_active = False
    revoke_user_refresh_tokens(db, user.id, reason="user_deactivated", commit=False)
    if commit:
        db.commit()


def _resolve_user(db: Session, email: str, organization_id: str | None) -> User | None:
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
    organization_id: str | None = None,
    settings: AuthSettings | None = None,
) -> IssuedTokens:
    settings = settings or get_settings().auth
    now = utc_now()
    user = _resolve_user(db, email, organization_id)

    # Capability-based, not provider-labeled: anyone holding a password hash may
    # password-login, INCLUDING accounts later linked to SSO (linking must add a
    # sign-in method, never silently revoke the password — an SSO-linked admin
    # who can no longer password-login is locked out of their own fallback).
    # SSO-only accounts (JIT or provisioned without a password) have no hash and
    # fail uniformly with unknown emails: never disclose which case it was.
    if user is None or not user.password_hash:
        auth_throttle.burn_password_check(password)
        raise _INVALID

    if auth_throttle.lock_expiry(user, now=now) is not None:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account temporarily locked after repeated failures. Try again later.",
        )

    if not security.verify_password(password, user.password_hash):
        if auth_throttle.record_failure(db, user, now=now, settings=settings) is not None:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account temporarily locked after repeated failures. Try again later.",
            )
        raise _INVALID

    # Success: reset throttle, stamp login, opportunistically upgrade the hash.
    auth_throttle.record_success(db, user, commit=False)
    user.last_login_at = now
    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
    db.commit()
    return issue_tokens(db, user, settings)


def _resolve_sso_user(
    db: Session, subject: str, email: str | None, organization_id: str
) -> User | None:
    # The verified connection's organization is mandatory on BOTH resolution
    # paths.  OIDC subjects and emails are not global tenant identifiers.
    linked_stmt = select(User).where(
        User.organization_id == organization_id,
        User.auth_provider == "oidc",
        User.sso_subject == subject,
        User.is_active.is_(True),
    )
    linked = db.scalars(linked_stmt).all()
    if len(linked) == 1:
        return linked[0]
    if len(linked) > 1:
        return None
    # First SSO login: match a pre-provisioned account by email (no auto-provisioning —
    # an unknown identity is rejected, so only invited users get in).
    if not email:
        return None
    email_stmt = select(User).where(
        User.organization_id == organization_id,
        User.email == email,
        User.is_active.is_(True),
    )
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


def _inactive_user_by_email(db: Session, organization_id: str, email: str) -> User | None:
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
    organization_hint: str | None = None,
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

    connection = sso_config.find_enabled_by_issuer_audience(
        db,
        issuer=str(hints.get("iss", "")),
        audience=hints.get("aud", ""),
    )
    if connection is None:
        raise _SSO_INVALID

    try:
        claims = security.verify_oidc_id_token(
            id_token, issuer=connection.issuer, audience=connection.client_id
        )
    except security.AuthError as exc:
        raise _SSO_INVALID from exc

    # Compatibility-only hints never select a tenant.  Check them only after
    # signature/issuer/audience verification and fail generically on mismatch;
    # silently overriding in either direction would preserve an authority bug.
    if organization_hint is not None and organization_hint != connection.organization_id:
        raise _SSO_INVALID

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

    organization_id = connection.organization_id
    user = _resolve_sso_user(db, str(claims["sub"]), email, organization_id)
    if user is None and connection.jit_enabled and connection.allowed_email_domains and email:
        # Access-request flow: record (or re-acknowledge) a deactivated stub and
        # refuse the session — approval is an explicit admin act. A previously
        # REJECTED stub becomes a fresh request again (the rejection is cleared).
        stub = _inactive_user_by_email(db, connection.organization_id, str(email))
        if stub is None:
            _record_sso_access_request(db, connection=connection, claims=claims)
        elif stub.access_rejected_at is not None:
            stub.access_rejected_at = None
            db.commit()
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
    return issue_tokens(db, user, settings)


# -- SSO access requests (JIT stubs awaiting admin approval) -------------------
def _access_request_stmt(organization_id: str):  # noqa: ANN202 - sqlalchemy Select
    """A pure JIT stub: deactivated, OIDC-linked, never logged in, no password.
    Deliberately narrow so admin-deactivated (offboarded) accounts never show
    up as approvable requests."""
    return select(User).where(
        User.organization_id == organization_id,
        User.is_active.is_(False),
        User.auth_provider == "oidc",
        User.password_hash.is_(None),
        User.last_login_at.is_(None),
        User.access_rejected_at.is_(None),  # a rejected stub is not an open request
    )


def list_sso_access_requests(db: Session, organization_id: str) -> list[User]:
    return list(db.scalars(_access_request_stmt(organization_id).order_by(User.created_at)))


def _get_access_request(db: Session, organization_id: str, user_id: UUID) -> User:
    user = db.scalar(_access_request_stmt(organization_id).where(User.id == user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Access request not found."
        )
    return user


def _provision_signer_identity(db: Session, user: User) -> None:
    """Mint the permanent signer identity when access is granted.

    Best-effort: an unconfigured SIGNER_ID_PEPPER must not block onboarding a
    user, and the signing path provisions lazily anyway. Failure is logged, not
    raised (docs/attestation_esignature.md §2.4).
    """
    from app.api.deps import TenantContext  # noqa: PLC0415 - avoids an import cycle
    from app.services.attestation.identity import (  # noqa: PLC0415
        SignerIdentityError,
        ensure_signer_identity,
    )

    try:
        ensure_signer_identity(db, TenantContext(organization_id=user.organization_id), user.id)
    except SignerIdentityError as exc:
        logger.warning("signer identity not provisioned for {}: {}", user.id, exc)


def approve_sso_access_request(
    db: Session, *, organization_id: str, user_id: UUID, role: str
) -> User:
    """The authorization act: an admin activates the requested account with an
    explicitly chosen role."""
    user = _get_access_request(db, organization_id, user_id)
    user.role = role
    user.is_active = True
    db.commit()
    db.refresh(user)
    # Access granted is the moment the signer identity should exist — before it
    # is ever needed, so an operator can print a signer roster in advance.
    _provision_signer_identity(db, user)
    return user


def reject_sso_access_request(db: Session, *, organization_id: str, user_id: UUID) -> None:
    """Records the rejection on the never-activated stub and leaves it
    deactivated. Users are never physically deleted (signer identities reference
    them and the append-only privilege tiering makes a DELETE fail on the
    primary — found 2026-08-16); the employee can request again, which clears
    the rejection and re-opens the request."""
    user = _get_access_request(db, organization_id, user_id)
    user.access_rejected_at = utc_now()
    db.commit()


def _claimed_uuid(claims: dict[str, object], key: str) -> UUID | None:
    raw = claims.get(key)
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _claimed_token_id(claims: dict[str, object]) -> UUID | None:
    return _claimed_uuid(claims, "jti")


def _claimed_subject(claims: dict[str, object]) -> UUID | None:
    """The user the token claims to belong to.

    Used ONLY to name the row to lock first (:func:`_lock_session_owner`), which
    is why an unverifiable value is harmless here: the record's real ``user_id``
    is compared against this same claim before anything is issued, so a claim
    that names the wrong user cannot get past that check — it can only fail to
    take a lock it was never going to need.
    """
    return _claimed_uuid(claims, "sub")


def _lock_refresh_token(db: Session, token_id: UUID) -> RefreshToken | None:
    """Load the token's row FOR UPDATE.

    Concurrency semantics, chosen deliberately: two refreshes of the SAME token
    are SERIALIZED here, so the read-modify-write of ``rotated_at`` can never
    interleave and two callers can never both believe they were first. Whichever
    loses the lock then sees ``rotated_at`` already set and is classified by the
    grace window (below), not by a race. On SQLite (the hermetic suite)
    SQLAlchemy omits the clause and the database's own write lock serializes the
    transaction, so the ordering guarantee holds either way.
    """
    return db.scalar(select(RefreshToken).where(RefreshToken.id == token_id).with_for_update())


def refresh_tokens(
    db: Session, *, refresh_token: str, settings: AuthSettings | None = None
) -> IssuedTokens:
    """Exchange a refresh token for a fresh pair, rotating and retiring the old one.

    Every failure answers the same generic 401. The interesting branch is reuse:
    a token that has ALREADY been rotated is either the client racing itself (two
    parallel refreshes of one stored token — the dashboard does this) or a stolen
    copy being replayed. Inside the grace window we assume the former and issue a
    sibling into the same family; outside it we assume the latter and revoke the
    entire family, which logs out the thief AND the real user. That is the point:
    a stolen token cannot outlive the moment either party next rotates.
    """
    settings = settings or get_settings().auth
    try:
        claims = security.decode_token(refresh_token, expected_type="refresh", settings=settings)
    except security.AuthError as exc:
        raise _INVALID_REFRESH from exc

    token_id = _claimed_token_id(claims)
    if token_id is None:
        raise _INVALID_REFRESH

    # users BEFORE refresh_tokens, always — this is the lock order the revoking
    # paths take, and taking it here (rather than inside `issue_tokens`, which
    # runs after the ancestor is already locked) is what keeps the two from
    # deadlocking (audit finding D-30).
    subject_id = _claimed_subject(claims)
    if subject_id is not None:
        _lock_session_owner(db, subject_id)

    now = utc_now()
    record = _lock_refresh_token(db, token_id)
    # Fail closed. No server-side state means the token cannot be rotated or
    # revoked, so it is not a credential — whether it is a forgery, a row that
    # was purged, or a session that predates this feature.
    if record is None:
        raise _INVALID_REFRESH
    # The digest binds the row to the exact token bytes: knowing a jti is not
    # enough to refresh with.
    if not hmac.compare_digest(record.token_hash, _hash_refresh_token(refresh_token)):
        raise _INVALID_REFRESH
    if record.revoked_at is not None or _as_aware(record.expires_at) <= now:
        raise _INVALID_REFRESH
    if str(record.user_id) != str(claims.get("sub")) or record.organization_id != str(
        claims.get("org")
    ):
        raise _INVALID_REFRESH

    if record.rotated_at is not None:
        elapsed = (now - _as_aware(record.rotated_at)).total_seconds()
        if elapsed > max(0, settings.refresh_rotation_grace_seconds):
            revoked = revoke_refresh_family(
                db, record.family_id, owner_id=record.user_id, reason="reuse_detected"
            )
            logger.warning(
                "Refresh-token reuse detected for user {} (org {}): revoked {} token(s) "
                "in family {}.",
                record.user_id,
                record.organization_id,
                revoked,
                record.family_id,
            )
            raise _INVALID_REFRESH
        logger.info(
            "Concurrent refresh within the {}s grace window (family {}).",
            settings.refresh_rotation_grace_seconds,
            record.family_id,
        )

    user = db.scalar(
        select(User).where(
            User.id == record.user_id,
            User.organization_id == record.organization_id,
            User.is_active.is_(True),
        )
    )
    if user is None:
        # Deactivated (or gone) between issue and refresh: end the lineage rather
        # than leaving live rows behind a door that is already shut.
        revoke_refresh_family(
            db, record.family_id, owner_id=record.user_id, reason="user_deactivated"
        )
        raise _INVALID_REFRESH
    if int(claims["authv"]) != user.authorization_version:
        revoke_refresh_family(
            db,
            record.family_id,
            owner_id=record.user_id,
            reason="authorization_changed",
        )
        raise _INVALID_REFRESH
    return issue_tokens(db, user, settings, family_id=record.family_id, rotated_from=record)


def logout(db: Session, *, refresh_token: str, settings: AuthSettings | None = None) -> None:
    """End the session the presented refresh token belongs to.

    The whole family goes, not just this token: signing out on one device must
    not leave a rotated ancestor usable. Deliberately silent and idempotent — an
    unknown, expired or already-revoked token is a no-op, so the endpoint is
    never an oracle for whether a token is valid.
    """
    settings = settings or get_settings().auth
    try:
        claims = security.decode_token(refresh_token, expected_type="refresh", settings=settings)
    except security.AuthError:
        return
    token_id = _claimed_token_id(claims)
    if token_id is None:
        return
    record = db.scalar(select(RefreshToken).where(RefreshToken.id == token_id))
    if record is None or not hmac.compare_digest(
        record.token_hash, _hash_refresh_token(refresh_token)
    ):
        return
    revoke_refresh_family(db, record.family_id, owner_id=record.user_id, reason="logout")


def set_password(db: Session, user: User, password: str, *, commit: bool = True) -> None:
    """Set (or reset) a user's password, clear any lockout, and END every session.

    The revocation is the point of the reset after a suspected compromise: before
    P0-5 the new password locked nobody out, because the attacker's refresh token
    kept minting access tokens for the rest of its 14 days.
    """
    user.password_hash = security.hash_password(password)
    user.auth_provider = "password"
    user.failed_login_attempts = 0
    user.locked_until = None
    revoke_user_refresh_tokens(db, user.id, reason="password_change", commit=False)
    if commit:
        db.commit()


def _as_aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
