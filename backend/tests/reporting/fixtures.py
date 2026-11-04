"""Regulatory reporting test fixtures, registered for the whole suite by ``tests/conftest.py``."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, SensitivityScope
from tests.support.authority import grant_organization_analyst


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
