"""Resolve a sign-in's tenant before any tenant context exists.

Password sign-in, SSO routing and token refresh begin with no organization, while
every account and SSO connection sits behind forced row-level security. Under a
login that does not bypass RLS (production's API credential, and ``aqs_api``)
those tables read as empty until a tenant is chosen. These helpers ask the narrow
``SECURITY DEFINER`` functions from migration ``202611210119`` which tenant a
credential belongs to, then enter that tenant so the rest of the flow reads and
writes under ordinary RLS.

Where the current login cannot execute those functions (SQLite, a database before
that migration, or a login that already bypasses RLS) the plain queries see every
tenant and are used unchanged.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.worker_access import has_worker_function
from app.identity.models.sso_connection import SsoConnection
from app.identity.models.user import User
from app.identity.service import sso_config
from app.models.audit_event import AuditEvent

LOGIN_SIGNATURE = "aqs_auth_login_organizations(text)"
SSO_SIGNATURE = "aqs_auth_sso_organizations(text,text[])"
ENABLED_SSO_SIGNATURE = "aqs_auth_enabled_sso_organizations()"
FAILURE_SIGNATURE = "aqs_auth_record_unattributed_failure(text,uuid,jsonb,timestamp with time zone)"


def _organizations(db: Session, statement: str, parameters: Mapping[str, object]) -> list[str]:
    rows = cast(Sequence[object], db.scalars(text(statement), dict(parameters)).all())
    return [str(row) for row in rows]


def login_organizations(db: Session, email: str) -> list[str]:
    """One entry per active account with exactly this email, in any tenant."""
    if has_worker_function(db, LOGIN_SIGNATURE):
        return _organizations(
            db, "SELECT * FROM aqs_auth_login_organizations(:email)", {"email": email}
        )
    statement = select(User.organization_id).where(User.email == email, User.is_active.is_(True))
    return list(db.scalars(statement).all())


def sso_organizations(db: Session, *, issuer: str, audience: object) -> list[str]:
    """One entry per enabled SSO connection an unverified issuer/audience routes to."""
    audiences = sso_config.routing_audiences(audience)
    if not audiences:
        return []
    stored_issuer = sso_config.routing_issuer(issuer)
    if has_worker_function(db, SSO_SIGNATURE):
        return _organizations(
            db,
            "SELECT * FROM aqs_auth_sso_organizations(:issuer, CAST(:audiences AS text[]))",
            {"issuer": stored_issuer, "audiences": audiences},
        )
    statement = select(SsoConnection.organization_id).where(
        SsoConnection.enabled.is_(True),
        SsoConnection.issuer == stored_issuer,
        SsoConnection.client_id.in_(audiences),
    )
    return list(db.scalars(statement).all())


def enabled_sso_organizations(db: Session) -> list[str]:
    """One entry per enabled SSO connection, in any tenant."""
    if has_worker_function(db, ENABLED_SSO_SIGNATURE):
        return _organizations(db, "SELECT * FROM aqs_auth_enabled_sso_organizations()", {})
    statement = select(SsoConnection.organization_id).where(SsoConnection.enabled.is_(True))
    return list(db.scalars(statement).all())


def enter_organization(db: Session, organization_id: str) -> None:
    """Scope this pre-sign-in session to the credential's tenant from now on.

    ``set_tenant_rls_context`` re-applies ``session.info`` when every later
    transaction begins, so the scope survives the commits sign-in performs; the
    open transaction is scoped here.
    """
    db.info["organization_id"] = organization_id
    if "bank_id" in db.info:
        del db.info["bank_id"]
    if db.get_bind().dialect.name == "postgresql":
        _ = db.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        _ = db.execute(text("SELECT set_config('app.bank_id', '', true)"))


def enter_sso_organization(db: Session, *, issuer: str, audience: object) -> None:
    """Enter the tenant of the one SSO connection the hints route to, if exactly one."""
    organizations = sso_organizations(db, issuer=issuer, audience=audience)
    if len(organizations) == 1:
        enter_organization(db, organizations[0])


def enter_enabled_sso_organization(db: Session) -> None:
    """Enter the tenant of the deployment's single enabled SSO connection.

    Several enabled connections are refused exactly as
    :func:`sso_config.resolve_client_config` refuses them.
    """
    organizations = enabled_sso_organizations(db)
    if len(organizations) > 1:
        raise sso_config.MULTIPLE_ENABLED_CONNECTIONS
    if organizations:
        enter_organization(db, organizations[0])


def record_unattributed_failure(
    db: Session,
    *,
    event_type: str,
    attempt_id: UUID,
    details: dict[str, JsonValue],
    occurred_at: dt.datetime,
) -> None:
    """Audit a sign-in attempt that matched no account, so has no tenant."""
    if has_worker_function(db, FAILURE_SIGNATURE):
        _ = db.execute(
            text(
                "SELECT aqs_auth_record_unattributed_failure("
                ":event_type, :attempt_id, CAST(:details AS jsonb), :occurred_at)"
            ),
            {
                "event_type": event_type,
                "attempt_id": attempt_id,
                "details": json.dumps(details),
                "occurred_at": occurred_at,
            },
        )
        return
    db.add(
        AuditEvent(
            organization_id=None,
            actor_user_id=None,
            event_type=event_type,
            entity_type="authentication_attempt",
            entity_id=str(attempt_id),
            created_at=occurred_at,
            details=details,
        )
    )
