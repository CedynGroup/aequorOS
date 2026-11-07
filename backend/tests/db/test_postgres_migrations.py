"""Tests for test_postgres_migrations."""

from __future__ import annotations

import pytest

from tests.support.postgres_migrations import postgres_administrator_url


@pytest.mark.parametrize("required", [False, True])
def test_missing_superuser_credentials_follow_privilege_policy(
    monkeypatch: pytest.MonkeyPatch, required: bool
) -> None:
    monkeypatch.delenv("POSTGRES_ADMIN_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_PRIVILEGE_TESTS_REQUIRED", "1" if required else "")
    outcome = pytest.fail.Exception if required else pytest.skip.Exception
    with pytest.raises(outcome, match="Superuser credentials are required"):
        postgres_administrator_url()
