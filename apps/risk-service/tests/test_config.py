from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    settings = Settings()

    assert settings.app.app_env == "local"
    assert settings.app.app_name == "risk-service"
    assert settings.database.database_url is None
    assert settings.cors.origins == []
    assert settings.logging.log_level == "INFO"


def test_settings_read_existing_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_NAME", "risk-service-staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/risk")
    monkeypatch.setenv("CORS_ORIGINS", " http://localhost:3000, ,http://localhost:3001 ")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings()

    assert settings.app.app_env == "staging"
    assert settings.app.app_name == "risk-service-staging"
    assert (
        settings.database.database_url
        == "postgresql+psycopg://postgres:postgres@localhost:5432/risk"
    )
    assert settings.cors.origins == ["http://localhost:3000", "http://localhost:3001"]
    assert settings.logging.log_level == "DEBUG"


def test_get_settings_is_cached_until_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "first-name")
    get_settings.cache_clear()
    first_settings = get_settings()

    monkeypatch.setenv("APP_NAME", "second-name")
    cached_settings = get_settings()

    get_settings.cache_clear()
    refreshed_settings = get_settings()

    assert cached_settings is first_settings
    assert cached_settings.app.app_name == "first-name"
    assert refreshed_settings is not first_settings
    assert refreshed_settings.app.app_name == "second-name"
