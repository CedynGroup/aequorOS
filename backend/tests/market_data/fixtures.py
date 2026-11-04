"""Market data test fixtures, registered for the whole suite by ``tests/conftest.py``."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, SensitivityScope
from tests.support.authority import grant_organization_analyst


@pytest.fixture
def markets_analyst_authority(db_session: Session) -> None:
    """Opt-in Markets analyst authority (every sensitivity) for USER_1.

    Covers manual uploads (published create), private curve overlays and
    implied-rating runs (confidential create/edit/run), and connection
    metadata reads (restricted view) in one organization-wide row.
    """
    grant_organization_analyst(
        db_session,
        ModuleScope.MARKETS,
        SensitivityScope.ALL,
        grantor="markets-analyst-fixture",
        reason="Authorize the integration fixture Markets mutations",
    )
