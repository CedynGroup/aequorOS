"""credit test fixtures, registered for the whole suite by ``tests/conftest.py``."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, SensitivityScope
from tests.support.authority import grant_organization_analyst


@pytest.fixture
def credit_run_authority(db_session: Session) -> None:
    """Opt-in credit calculation authority for integration fixtures using USER_1.

    The hermetic baseline sentence is ``viewer / all / all``, which carries no
    ``run``: after the P4-C cutover
    (``backend/docs/credit_enforcement_rollout.md``) sealing a credit baseline
    needs an Analyst CREDIT/confidential row, exactly as FX, IRRBB and FTP do.
    """
    grant_organization_analyst(
        db_session,
        ModuleScope.CREDIT,
        SensitivityScope.CONFIDENTIAL,
        grantor="credit-calculation-fixture",
        reason="Authorize the integration fixture credit calculations",
    )
