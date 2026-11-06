"""Opt-in identity rows and complete grants using the production services.

Nothing here adds baseline membership, owner reads, modules or financial data.
The caller owns the transaction, except ``tenant_session`` uses the real
token-issuance service, which commits its refresh-token record.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.authorization import (
    DataScope,
    InstitutionScope,
    ModuleScope,
    PrincipalLocator,
    RoleBundle,
    SensitivityScope,
)
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.bank import Bank
from app.identity.models.user import User
from app.identity.service import authentication, authorization
from app.models import Jurisdiction, Organization


def organization(db: Session, *, name: str) -> Organization:
    row = Organization(name=name)
    db.add(row)
    db.flush()
    return row


def bank(
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    jurisdiction: Jurisdiction,
    institution_type: str,
) -> Bank:
    """An empty institution, with currency from its explicit registry row."""
    row = Bank(
        id=bank_id,
        organization_id=organization_id,
        name="Synthetic Bank",
        short_name="Synthetic",
        jurisdiction_code=jurisdiction.code,
        currency=jurisdiction.currency_code,
        institution_type=institution_type,
        license_type=institution_type,
    )
    db.add(row)
    db.flush()
    return row


def human(db: Session, *, organization_id: str, legacy_role: str) -> User:
    """A new active human, with a required scalar role but NO binding."""
    row = User(
        organization_id=organization_id,
        email=f"{uuid4()}@actor.example.test",
        display_name="Synthetic human",
        role=legacy_role,
        auth_provider="oidc",
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def machine(db: Session, *, organization_id: str, legacy_role: str) -> User:
    """A service identity only; this does not issue an integration key."""
    row = User(
        organization_id=organization_id,
        email=f"{uuid4()}@machine.example.test",
        display_name="Synthetic machine",
        role=legacy_role,
        auth_provider="service",
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def institution_scope(
    *,
    bank_id: str,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    data_scope: DataScope,
    data_scope_values: tuple[str, ...] = (),
) -> authorization.BindingScope:
    return authorization.BindingScope(
        InstitutionScope.INSTITUTION,
        bank_id,
        module,
        sensitivity,
        data_scope,
        data_scope_values,
    )


def organization_scope(
    *, module: ModuleScope, sensitivity: SensitivityScope
) -> authorization.BindingScope:
    """Explicitly opt into organization-wide, whole-institution coverage."""
    return authorization.BindingScope(
        InstitutionScope.ORGANIZATION, None, module, sensitivity, DataScope.ALL
    )


@dataclass(frozen=True)
class RoleBindingFactory:
    """Bind a session and explicit grantor, never any permission defaults.

    This is the low-level grant service, NOT the Owner administration API:
    delegation, public-grant restrictions and assignment SoD tests must call
    that API themselves.
    """

    db: Session
    grantor: authorization.GrantorRef

    def create(
        self,
        *,
        principal: PrincipalLocator,
        bundle: RoleBundle,
        scope: authorization.BindingScope,
        reason: str,
    ) -> AuthorizationBinding:
        """Create one complete sentence without resetting authv or committing."""
        return authorization.create_role_binding(
            self.db,
            organization_id=principal.organization_id,
            principal_user_id=principal.principal_id,
            principal_type=principal.principal_type,
            role_bundle=bundle,
            scope=scope,
            grantor=self.grantor,
            reason=reason,
            commit=False,
        )


def tenant_session(db: Session, *, user: User) -> authentication.IssuedTokens:
    """Record a real human access/refresh session at the current authv.

    Machine identities must use integration-key issuance, never a human JWT.
    No cached token is silently renewed after a grant.
    """
    if user.auth_provider == "service":
        raise ValueError("machine identities require integration-key credentials")
    return authentication.issue_tokens(db, user)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
