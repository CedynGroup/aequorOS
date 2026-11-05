"""Credential cleanup boundaries, batching and scheduled reachability.

This module's other-suite cases live in ``test_session_retention_integration.py``.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.core.config import AuthSettings, get_settings


@given(
    days=st.integers(min_value=1, max_value=3650), batch=st.integers(min_value=1, max_value=10000)
)
def test_retention_settings_accept_positive_windows_and_bounded_batches(
    days: int, batch: int
) -> None:
    settings = AuthSettings(
        AUTH_REFRESH_TOKEN_RETENTION_DAYS=days, AUTH_SESSION_PURGE_BATCH_SIZE=batch
    )
    assert settings.refresh_token_retention_days == days
    assert settings.session_purge_batch_size == batch


@pytest.mark.parametrize(
    "options",
    [
        {"AUTH_REFRESH_TOKEN_RETENTION_DAYS": 0},
        {"AUTH_REFRESH_TOKEN_RETENTION_DAYS": -1},
        {"AUTH_SESSION_PURGE_BATCH_SIZE": 0},
        {"AUTH_SESSION_PURGE_BATCH_SIZE": 10001},
    ],
)
def test_retention_settings_reject_unbounded_or_nonpositive_values(options: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        AuthSettings.model_validate(options)


@pytest.mark.parametrize("enabled", ["true", "false"])
@pytest.mark.parametrize("days", [1, 30, 90])
def test_settings_loading_refuses_retention_shorter_than_rotation_grace(
    monkeypatch: pytest.MonkeyPatch, enabled: str, days: int
) -> None:
    monkeypatch.setenv("AUTH_SESSION_PURGE_ENABLED", enabled)
    monkeypatch.setenv("AUTH_REFRESH_TOKEN_RETENTION_DAYS", str(days))
    monkeypatch.setenv("AUTH_REFRESH_ROTATION_GRACE", str(days * 86400 + 1))
    get_settings.cache_clear()
    with pytest.raises(
        ValidationError,
        match="AUTH_REFRESH_TOKEN_RETENTION_DAYS must cover AUTH_REFRESH_ROTATION_GRACE",
    ):
        get_settings()
