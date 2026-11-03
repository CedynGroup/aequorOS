"""Forecasting test fixtures, registered for the whole suite by ``tests/conftest.py``."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, SensitivityScope
from tests.support.authority import grant_organization_analyst


@pytest.fixture
def forecasting_run_authority(db_session: Session) -> None:
    """Opt-in Forecasting run authority (projection, optimizer, what-if, reverse stress)."""
    grant_organization_analyst(
        db_session,
        ModuleScope.FORECASTING,
        SensitivityScope.CONFIDENTIAL,
        grantor="forecasting-calculation-fixture",
        reason="Authorize the integration fixture Forecasting calculations",
    )
