"""Operator app dependencies: identity, DB sessions, audit helper.

Staff identity MATCHES the client-side model (founder's directive,
2026-08-11: "We are building the same as client"): email+password against
``operator_users`` is the PRIMARY path (an 8-hour HS256 session JWT over the
dedicated ``OPERATOR_JWT_SECRET``), workforce OIDC (Google Workspace/Okta,
verified with the same zero-trust machinery customer SSO uses) is the
SECONDARY path, and — outside production only — a static development bearer
token remains for local work. There is still NO overlap with tenant identity
(staff_UI.md §1: workforce and customer identity are separate systems);
``operator_users`` is a staff table, not a tenant one.

Bearer resolution order: dev token, operator JWT, OIDC id_token. A token that
VERIFIES as an operator JWT but fails the row check (missing, deactivated,
stale role claim) is rejected outright — it never falls through to OIDC.

The DB session here is CROSS-TENANT: it deliberately sets no
``organization_id`` on the session, so on the RLS-forced primary it must run
as a BYPASSRLS role (``OPERATOR_DATABASE_URL``, the worker-role precedent).
Consequently EVERY query issued through it must carry an explicit
``organization_id`` filter — that is the #1 cross-tenant risk the staff
console spec calls out, and the read services in this package are written to
that rule.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core import security
from app.core.config import (
    OperatorSettings,
    get_operator_settings,
    get_settings,
    is_undeployed_environment,
)
from app.db.session import get_engine
from app.models import OperatorAuditLog, OperatorUser
from app.models.operator import OPERATOR_ROLE_RANK
from app.operator.services import operator_auth

_bearer_scheme = HTTPBearer(
    auto_error=False,
    description=(
        "Operator credential: operator session JWT (password sign-in), "
        "workforce OIDC id_token, or the dev token outside production"
    ),
)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Operator authentication failed.",
    headers={"WWW-Authenticate": "Bearer"},
)

OperatorRole = Literal["developer", "operator_admin", "super_admin"]


@dataclass(frozen=True)
class OperatorContext:
    """The authenticated staff identity for one operator request."""

    email: str
    auth_mode: Literal["dev", "oidc", "password"]
    #: Staff authorization role. Password/JWT sessions carry it as a claim
    #: verified against the ``operator_users`` row; OIDC sessions take the
    #: row's role (or ``developer`` when domain-allow-listed without a row);
    #: dev sessions are ``super_admin`` — the local root session IS the
    #: documented bootstrap path for creating the first operator account,
    #: and dev auth cannot exist in production (boot refusal + request-level
    #: check).
    role: OperatorRole


def _dev_context(token: str, operator_settings: OperatorSettings) -> OperatorContext | None:
    """Dev-token auth: enabled, token configured, matching — and ONLY on an
    UNDEPLOYED environment (belt to the boot-refusal braces in
    ``create_operator_app``).

    This asked ``app_env == "production"`` until 2026-08-23, which admitted a
    static shared secret as ``super_admin`` on ``staging`` — a deployed host
    on the same primary database, reached by the same cross-tenant BYPASSRLS
    session. "Not production" is an unbounded set whose default branch is the
    dangerous one; the allow-list in :func:`is_undeployed_environment` inverts
    it, so anything that is not ``local``/``test`` is treated as deployed.
    """
    if not operator_settings.dev_auth_enabled:
        return None
    if not is_undeployed_environment():
        return None
    if operator_settings.dev_token is None:
        return None
    if not secrets.compare_digest(token, operator_settings.dev_token):
        return None
    return OperatorContext(
        email=operator_settings.dev_email, auth_mode="dev", role="super_admin"
    )


def _load_operator_user(email: str) -> OperatorUser | None:
    """Fetch one staff row on a short-lived session (global table, no RLS)."""
    session = get_operator_sessionmaker()()
    try:
        return session.scalar(select(OperatorUser).where(OperatorUser.email == email))
    finally:
        session.close()


def _password_context(token: str, operator_settings: OperatorSettings) -> OperatorContext | None:
    """Operator session JWT (the email+password primary path).

    Returns None when the bearer is not an operator JWT at all (unconfigured
    secret, wrong signature/typ — an OIDC id_token lands here too, and falls
    through to the OIDC branch). Raises the generic 401 when the token IS a
    valid operator JWT but the account no longer backs it: the row is the
    persistent authority, so a deactivated operator or a stale role claim
    dies immediately rather than riding out the token's 8 hours.
    """
    if operator_settings.jwt_secret is None:
        return None
    try:
        claims = operator_auth.verify_operator_token(token, secret=operator_settings.jwt_secret)
    except security.TokenInvalidError:
        return None
    email = str(claims["sub"]).lower()
    user = _load_operator_user(email)
    if user is None or not user.is_active or user.role != claims.get("role"):
        raise _UNAUTHORIZED
    return OperatorContext(email=email, auth_mode="password", role=cast("OperatorRole", user.role))


def _oidc_context(token: str, operator_settings: OperatorSettings) -> OperatorContext:
    if operator_settings.oidc_issuer is None or operator_settings.oidc_client_id is None:
        # Fail closed with the house 401 envelope: an unconfigured workforce
        # IdP means nobody authenticates, not that anybody does.
        raise _UNAUTHORIZED
    try:
        claims = security.verify_oidc_id_token(
            token,
            issuer=operator_settings.oidc_issuer,
            audience=operator_settings.oidc_client_id,
        )
    except security.AuthError as exc:
        raise _UNAUTHORIZED from exc
    email = claims.get("email")
    # Workforce identity is stricter than customer SSO: the email must exist
    # AND be positively verified (no Entra-style benefit of the doubt) AND sit
    # under the allowed workforce domain.
    if not isinstance(email, str) or claims.get("email_verified") is not True:
        raise _UNAUTHORIZED
    domain = email.rsplit("@", 1)[-1].lower()
    if domain != operator_settings.oidc_allowed_domain.lower():
        raise _UNAUTHORIZED
    # Parity with the client model: when a staff row exists for this email it
    # is the authority — a deactivated operator cannot slip back in through
    # SSO, and the row's role governs. A domain-allowed identity WITHOUT a
    # row keeps the historical allow-list behavior (documented): it
    # authenticates with the base 'developer' role.
    normalized = email.lower()
    user = _load_operator_user(normalized)
    if user is not None and not user.is_active:
        raise _UNAUTHORIZED
    role: OperatorRole = cast("OperatorRole", user.role) if user is not None else "developer"
    return OperatorContext(email=normalized, auth_mode="oidc", role=role)


def get_operator_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> OperatorContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    operator_settings = get_operator_settings()
    dev = _dev_context(credentials.credentials, operator_settings)
    if dev is not None:
        return dev
    password = _password_context(credentials.credentials, operator_settings)
    if password is not None:
        return password
    return _oidc_context(credentials.credentials, operator_settings)


def require_operator_admin(
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> OperatorContext:
    """Gate for operator-account management: ``operator_admin`` or above.

    Rank rules WITHIN management (who may touch which row) live in the
    operators feature via ``OPERATOR_ROLE_RANK``; this dependency only
    keeps ``developer`` sessions out entirely.
    """
    if OPERATOR_ROLE_RANK[operator.role] < OPERATOR_ROLE_RANK["operator_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator administration requires the operator_admin role or above.",
        )
    return operator


# -- database ------------------------------------------------------------------
def resolve_operator_database_url() -> str | None:
    """OPERATOR_DATABASE_URL → WORKER_DATABASE_URL → DATABASE_URL.

    Real deployments set OPERATOR_DATABASE_URL to the operator's own
    BYPASSRLS role; the fallbacks keep local/hermetic runs zero-config (the
    tenant-scoped app role would simply see zero rows on RLS-forced tables).
    """
    settings = get_settings()
    return (
        get_operator_settings().operator_database_url
        or settings.worker.worker_database_url
        or settings.database.database_url
    )


def get_operator_sessionmaker() -> sessionmaker:
    url = resolve_operator_database_url()
    if url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Operator database is not configured "
            "(OPERATOR_DATABASE_URL / WORKER_DATABASE_URL / DATABASE_URL).",
        )
    return sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False)


def get_operator_db_session(
    _ctx: Annotated[OperatorContext, Depends(get_operator_context)],
) -> Iterator[Session]:
    """Cross-tenant session, auth-gated: no session exists without an
    authenticated operator. No ``organization_id`` is set on the session —
    every query MUST scope itself explicitly."""
    session = get_operator_sessionmaker()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


Operator = Annotated[OperatorContext, Depends(get_operator_context)]
OperatorAdmin = Annotated[OperatorContext, Depends(require_operator_admin)]
OperatorDb = Annotated[Session, Depends(get_operator_db_session)]


# -- audit -----------------------------------------------------------------------
def record_operator_action(
    db: Session,
    operator: OperatorContext,
    *,
    action: str,
    target_org: str | None = None,
    detail: dict[str, Any] | None = None,
) -> OperatorAuditLog:
    """Queue an operator_audit_log row on the CURRENT transaction.

    Mutating endpoints call this before their commit so the action and its
    audit row land (or roll back) atomically. The caller owns the commit.
    Never put credential material — one-time passwords included — in
    ``detail``: the row is append-only and readable by every operator.
    """
    entry = OperatorAuditLog(
        operator_email=operator.email,
        auth_mode=operator.auth_mode,
        action=action,
        target_org=target_org,
        detail=detail or {},
    )
    db.add(entry)
    return entry
