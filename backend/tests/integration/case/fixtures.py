from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import Settings
from tests.conftest import FakeStorage
from tests.integration.case.factories import ServiceFactories


@pytest.fixture
def service_factories(
    db_session: Session,
    fake_storage: FakeStorage,
    test_settings: Settings,
    tenant_ctx: TenantContext,
) -> ServiceFactories:
    return ServiceFactories(db_session, fake_storage, test_settings, tenant_ctx)


def grant_case_workflow_authority(db_session: Session) -> None:
    """Explicit preparation and review grants for existing case regressions.

    Authorization tests remove these rows and exercise each permission alone.
    """
    from app.core.authorization import (  # noqa: PLC0415
        GrantorType,
        InstitutionScope,
        ModuleScope,
        PrincipalType,
        RoleBundle,
        SensitivityScope,
    )
    from app.identity.public import User  # noqa: PLC0415
    from app.identity.service import authorization  # noqa: PLC0415
    from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2  # noqa: PLC0415

    for organization_id, user_id in ((ORG_1, USER_1), (ORG_2, USER_2)):
        for bundle in (RoleBundle.ANALYST, RoleBundle.APPROVER):
            authorization.create_role_binding(
                db_session,
                organization_id=organization_id,
                principal_user_id=user_id,
                principal_type=PrincipalType.HUMAN,
                role_bundle=bundle,
                scope=authorization.BindingScope(
                    InstitutionScope.ORGANIZATION,
                    None,
                    ModuleScope.RISK,
                    SensitivityScope.ALL,
                ),
                grantor=authorization.GrantorRef(GrantorType.SYSTEM, "case-workflow-fixture"),
                reason="Explicit preparation and review authority for case regression fixtures",
                commit=False,
            )
        user = db_session.get(User, user_id)
        assert user is not None
        user.authorization_version = 1
    db_session.commit()


@pytest.fixture
def case_api_authority(db_session: Session) -> None:
    grant_case_workflow_authority(db_session)
