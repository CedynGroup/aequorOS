"""Regulatory reporting test fixtures, registered for the whole suite by ``tests/conftest.py``."""

from __future__ import annotations

import pytest
from sqlalchemy import select
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
from tests.support.authority import grant_organization_analyst
from tests.support.helpers import ORG_1, USER_1


@pytest.fixture
def return_generation_authority(db_session: Session) -> None:
    """Opt-in whole-institution Regulatory Reporting generation authority."""
    grant_organization_analyst(
        db_session,
        ModuleScope.REGULATORY,
        SensitivityScope.RESTRICTED,
        grantor="return-generation-fixture",
        reason="Authorize the integration fixture return generation",
    )


@pytest.fixture
def reporting_configure_authority(db_session: Session) -> None:
    """Opt-in whole-institution Regulatory Reporting configuration authority.

    Channel-config and reporting-settings writes are configure duties; the
    organization-scope binding covers every bank of the tenant.
    """
    authorization.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.CONFIGURATOR,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION,
            None,
            ModuleScope.REGULATORY,
            SensitivityScope.RESTRICTED,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "reporting-configure-fixture"),
        reason="Authorize the integration fixture reporting configuration",
        commit=False,
    )
    user = db_session.scalars(
        select(User).where(User.id == USER_1, User.organization_id == ORG_1)
    ).one()
    user.authorization_version = 1
    db_session.commit()
