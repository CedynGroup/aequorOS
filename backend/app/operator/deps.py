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
An OIDC identity likewise authenticates only when its verified, normalized
email matches an active ``operator_users`` row; the workforce domain check is
necessary identity evidence but grants no role by itself.

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
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy import JSON, String, bindparam, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.orm import Session, sessionmaker

from app.core import security
from app.core.audit_attribution import staff_bank_sql
from app.core.config import (
    OperatorSettings,
    get_operator_settings,
    get_settings,
    is_undeployed_environment,
)
from app.db.base import utc_now
from app.db.session import get_engine
from app.identity.public import Bank
from app.models import InstitutionType, OperatorAuditLog, OperatorInspectorSession, OperatorUser
from app.models.operator import OPERATOR_ROLE_RANK
from app.operator.services import operator_auth

_bearer_scheme = HTTPBearer(
    auto_error=False,
    description=(
        "Operator credential: operator session JWT (password sign-in), "
        "workforce OIDC id_token for an active provisioned operator, or the "
        "dev token outside production"
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
    #: active provisioned row's role.
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
    return OperatorContext(email=operator_settings.dev_email, auth_mode="dev", role="super_admin")


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
    # The staff row is the authority: domain membership verifies workforce
    # identity, but explicit active provisioning grants operator-plane access.
    normalized = email.lower()
    user = _load_operator_user(normalized)
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    return OperatorContext(email=normalized, auth_mode="oidc", role=cast("OperatorRole", user.role))


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
    return sessionmaker(
        bind=get_engine(url, "aequoros-operator"), autoflush=False, expire_on_commit=False
    )


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
def record_operator_action(  # noqa: PLR0913 - actor, scope, evidence and accessed resources
    db: Session,
    operator: OperatorContext,
    *,
    action: str,
    target_org: str | None = None,
    detail: dict[str, Any] | None = None,
    resources: BaseModel | list[dict[str, JsonValue]] | None = None,
) -> OperatorAuditLog:
    """Queue an operator_audit_log row on the CURRENT transaction.

    Mutating endpoints call this before their commit so the action and its
    audit row land (or roll back) atomically. The caller owns the commit.
    Never put credential material — one-time passwords included — in
    ``detail``: the row is append-only and readable by every operator.
    """
    evidence: dict[str, JsonValue] = dict(detail or {})
    session_id = evidence.get("session_id")
    if target_org is not None and isinstance(session_id, str):
        try:
            inspection_id = UUID(session_id)
        except ValueError:
            inspection_id = None
        if inspection_id is not None:
            inspection = db.scalar(
                select(OperatorInspectorSession).where(
                    OperatorInspectorSession.id == inspection_id,
                    OperatorInspectorSession.organization_id == target_org,
                    OperatorInspectorSession.started_by == operator.email,
                )
            )
            if inspection is not None:
                evidence.setdefault("reason", evidence.get("note") or inspection.reason)
                evidence.setdefault("support_reference", inspection.support_reference)
    if target_org is not None:
        bank_id = _retained_staff_bank(db, target_org, evidence)
        if bank_id is not None:
            evidence.setdefault("bank_id", bank_id)
    evidence.setdefault(
        "reason", evidence.get("note") or action.replace(".", " ").replace("_", " ")
    )
    evidence.setdefault("support_reference", None)
    evidence.setdefault("outcome", "failure" if evidence.get("succeeded") is False else "success")
    owners = _accessed_banks(db, action, target_org, evidence, resources)
    entries: list[OperatorAuditLog] = []
    marker = evidence.get("bank_id")
    targets: list[tuple[str | None, str | None]] = list(sorted(owners)) or [
        (target_org, marker if isinstance(marker, str) else None)
    ]
    for org, bank_id in targets:
        scoped = dict(evidence)
        if bank_id is not None:
            scoped["bank_id"] = bank_id
        if org is not None and session_id is None:
            inspection = db.scalar(
                select(OperatorInspectorSession)
                .where(
                    OperatorInspectorSession.organization_id == org,
                    OperatorInspectorSession.started_by == operator.email,
                    OperatorInspectorSession.ended_at.is_(None),
                    OperatorInspectorSession.expires_at > utc_now(),
                )
                .order_by(OperatorInspectorSession.started_at.desc())
                .limit(1)
            )
            if inspection is not None:
                scoped["session_id"] = str(inspection.id)
                scoped["reason"] = evidence.get("note") or inspection.reason
                scoped["support_reference"] = inspection.support_reference
        entry = OperatorAuditLog(
            operator_email=operator.email,
            auth_mode=operator.auth_mode,
            action=action,
            target_org=org,
            detail=scoped,
        )
        db.add(entry)
        entries.append(entry)
    return entries[0]


def _organization_surface(action: str, evidence: dict[str, JsonValue]) -> bool:
    """Operations that access shared tenant configuration or identity."""
    return (
        action
        in {
            "inspector.read.users",
            "inspector.read.entitlements",
            "inspector.read.storage",
            "inspector.session.start",
            "inspector.session.end",
            "inspector.act_token.mint",
            "inspector.sessions.read",
        }
        or action.startswith("inspector.entitlement.")
        or (action == "inspector.fix.config" and evidence.get("kind") == "threshold_value")
    )


def _resource_banks(
    db: Session, value: JsonValue, organization: str | None
) -> Iterator[tuple[str | None, str]]:
    """Collect identities from response resources, never copy their data to the log."""
    if isinstance(value, list):
        for item in value:
            yield from _resource_banks(db, item, organization)
    elif isinstance(value, dict):
        org = value.get("organization_id") or value.get("target_org") or organization
        org = org if isinstance(org, str) else organization
        bank = value.get("bank_id")
        if (
            bank is None
            and org is not None
            and (isinstance(value.get("batch_id"), str) or value.get("kind") == "mapping_active")
        ):
            bank = _retained_staff_bank(db, org, value)
        if isinstance(bank, str):
            yield org, bank
        for key, item in value.items():
            if key not in {"metrics", "config", "raw_record", "validation_report", "etl_report"}:
                yield from _resource_banks(db, item, org)


def _accessed_banks(
    db: Session,
    action: str,
    target_org: str | None,
    evidence: dict[str, JsonValue],
    resources: BaseModel | list[dict[str, JsonValue]] | None,
) -> set[tuple[str, str]]:
    owners: set[tuple[str, str]] = set()
    if resources is not None:
        payload: JsonValue = TypeAdapter[JsonValue](JsonValue).validate_python(
            resources.model_dump(mode="json") if isinstance(resources, BaseModel) else resources
        )
        for org, bank_id in _resource_banks(db, payload, target_org):
            bank = db.scalar(select(Bank).where(Bank.id == bank_id))
            if bank is not None and (org is None or bank.organization_id == org):
                owners.add((bank.organization_id, bank.id))
    elif (
        target_org is not None
        and evidence.get("bank_id") is None
        and _organization_surface(action, evidence)
    ):
        stmt = select(Bank).where(Bank.organization_id == target_org)
        after = evidence.get("after")
        if action == "inspector.fix.config" and isinstance(after, dict):
            jurisdiction = after.get("jurisdiction_code")
            if isinstance(jurisdiction, str):
                stmt = stmt.where(Bank.jurisdiction_code == jurisdiction)
            institution_class = after.get("institution_class")
            if isinstance(institution_class, str):
                stmt = stmt.join(
                    InstitutionType, InstitutionType.type_code == Bank.institution_type
                ).where(InstitutionType.institution_class == institution_class)
        owners.update((bank.organization_id, bank.id) for bank in db.scalars(stmt))
    return owners


def _retained_staff_bank(
    db: Session, organization: str, evidence: dict[str, JsonValue]
) -> str | None:
    return db.scalar(
        select(
            sql_cast(
                text(
                    staff_bank_sql(
                        ":org",
                        "CAST(:detail AS jsonb)"
                        if db.get_bind().dialect.name == "postgresql"
                        else ":detail",
                        postgresql=db.get_bind().dialect.name == "postgresql",
                    )
                ).bindparams(bindparam("detail", type_=JSON)),
                String,
            )
        ),
        {"org": organization, "detail": evidence},
    )
