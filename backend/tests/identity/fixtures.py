"""Explicit Identity authority used to construct synthetic institution registers."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, SensitivityScope
from tests.support.authority import grant_organization_analyst


@pytest.fixture
def institution_profile_authority(db_session: Session) -> None:
    """Register writers opt in independently of regulatory generation authority."""
    grant_organization_analyst(
        db_session,
        ModuleScope.INSTITUTION,
        SensitivityScope.ALL,
        grantor="institution-register-fixture",
        reason="Authorize synthetic institution register construction",
    )
