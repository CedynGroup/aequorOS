"""Case workflow regressions use explicit grants, independent of token roles."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def case_workflow_authority(request: pytest.FixtureRequest) -> None:
    if "db_client" in request.fixturenames:
        request.getfixturevalue("case_api_authority")
