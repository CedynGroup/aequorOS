"""FTP test fixtures, registered for the whole suite by ``tests/conftest.py``."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, SensitivityScope
from tests.support.authority import grant_organization_analyst


@pytest.fixture
def ftp_run_authority(db_session: Session) -> None:
    """Opt-in FTP calculation authority for integration fixtures using USER_1."""
    grant_organization_analyst(
        db_session,
        ModuleScope.FTP,
        SensitivityScope.CONFIDENTIAL,
        grantor="ftp-calculation-fixture",
        reason="Authorize the integration fixture FTP calculations",
    )
