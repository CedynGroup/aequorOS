"""Google-shaped signed tokens cross the real verifier and operator boundary.

This module's other-suite cases live in ``test_google_sign_in_integration.py``.
"""

from __future__ import annotations

import pytest

from app.core.config import get_operator_settings, get_settings


@pytest.mark.parametrize("environment", ["local", "test", "staging", "production", "preview"])
def test_google_policy_test_mode_is_undeployed_only(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("OPERATOR_OIDC_GOOGLE_POLICY_TEST_MODE", "true")
    get_settings.cache_clear()
    try:
        if environment in {"local", "test"}:
            settings = get_operator_settings()
            assert settings.uses_google_policy
        else:
            with pytest.raises(ValueError):
                _ = get_operator_settings()
    finally:
        get_settings.cache_clear()
        get_operator_settings.cache_clear()
