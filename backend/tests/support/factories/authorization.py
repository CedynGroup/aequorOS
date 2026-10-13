"""Explicit institution grants for fixture officers driving real lifecycles."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.identity.service import authorization
from app.models import User


def grant_institution_authority(  # noqa: PLR0913 - the fixture grant sentence is explicit
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    user_id: UUID,
    bundle: RoleBundle,
    module: ModuleScope = ModuleScope.REGULATORY,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
) -> None:
    """Give one officer an explicit whole-institution authorization sentence.

    Callers choose the bundle explicitly; legacy scalar roles grant nothing.
    Fixture contexts use authv=1 and are minted after this bootstrap transaction.
    """
    authorization.create_role_binding(
        db,
        organization_id=organization_id,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            bank_id,
            module,
            sensitivity,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "return-officer-fixture"),
        reason="Authorize the fixture officer's return lifecycle duties",
        commit=False,
    )
    user = db.get(User, user_id)
    assert user is not None
    user.authorization_version = 1
