"""Opt-in scoped authority for legacy enterprise stress workflow fixtures."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from tests.stress.authority import grant


@pytest.fixture
def stress_run_authority(db_session: Session) -> None:
    """Exercise the governed Risk workflow without relying on a scalar claim."""
    grant(db_session)
