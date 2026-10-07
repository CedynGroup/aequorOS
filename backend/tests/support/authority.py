"""Authority grants the feature fixtures give the demo user."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.models import User
from app.services import authorization
from tests.support.helpers import ORG_1, USER_1


def grant_organization_analyst(
    db_session: Session,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    *,
    grantor: str,
    reason: str,
) -> None:
    """Bind ``USER_1`` as an organization-wide Analyst on ``module`` and commit.

    No sessions exist at bootstrap, so fixture tokens and service contexts use
    ``authorization_version`` 1.
    """
    authorization.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ANALYST,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION,
            None,
            module,
            sensitivity,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, grantor),
        reason=reason,
        commit=False,
    )
    user = db_session.get(User, USER_1)
    assert user is not None
    user.authorization_version = 1
    db_session.commit()
