"""Operator app dependencies: identity, DB sessions, audit helper.

Identity comes from workforce OIDC (Google Workspace/Okta) verified with the
same zero-trust machinery customer SSO uses, or — outside production only —
a static development bearer token. There is NO password path and NO overlap
with tenant identity (staff_UI.md §1: workforce and customer identity are
separate systems).

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
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from app.core import security
from app.core.config import OperatorSettings, get_operator_settings, get_settings
from app.db.session import get_engine
from app.models import OperatorAuditLog

_bearer_scheme = HTTPBearer(
    auto_error=False,
    description=(
        "Operator credential: workforce OIDC id_token "
        "(or the dev token outside production)"
    ),
)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Operator authentication failed.",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class OperatorContext:
    """The authenticated staff identity for one operator request."""

    email: str
    auth_mode: Literal["dev", "oidc"]


def _dev_context(token: str, operator_settings: OperatorSettings) -> OperatorContext | None:
    """Dev-token auth: enabled, token configured, matching — and NEVER in
    production (belt to the boot-refusal braces in ``create_operator_app``)."""
    if not operator_settings.dev_auth_enabled:
        return None
    if get_settings().app.app_env == "production":
        return None
    if operator_settings.dev_token is None:
        return None
    if not secrets.compare_digest(token, operator_settings.dev_token):
        return None
    return OperatorContext(email=operator_settings.dev_email, auth_mode="dev")


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
    return OperatorContext(email=email.lower(), auth_mode="oidc")


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
    return _oidc_context(credentials.credentials, operator_settings)


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
