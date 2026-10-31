"""Explicit grants for stress workflow fixtures, separate from role claims."""

from __future__ import annotations

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
from app.identity.models.user import User
from app.identity.service import authorization
from tests.support.helpers import ORG_1, USER_1


def grant(  # noqa: PLR0913 - explicit binding dimensions for test scenarios
    db: Session,
    role: RoleBundle = RoleBundle.ANALYST,
    *,
    user_id: UUID = USER_1,
    bank_id: str | None = None,
    module: ModuleScope = ModuleScope.RISK,
    sensitivity: SensitivityScope = SensitivityScope.CONFIDENTIAL,
) -> UUID:
    binding = authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=role,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION if bank_id is None else InstitutionScope.INSTITUTION,
            bank_id,
            module,
            sensitivity,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "stress-workflow-test"),
        reason="Explicit authority for this stress workflow test",
        commit=False,
    )
    user = db.get(User, user_id)
    assert user is not None
    user.authorization_version = 1
    db.commit()
    return binding.id
