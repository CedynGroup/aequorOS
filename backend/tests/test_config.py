from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings, get_settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Run from an empty directory so a developer's local .env (created per the
    # README) cannot shadow the true defaults under test.
    monkeypatch.chdir(tmp_path)
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
    # The e-sign requirement ships ON; disabling it is an explicit deployment act.
    assert settings.attestation.esign_required is True


def test_settings_read_existing_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_NAME", "risk-service-staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/risk")
    monkeypatch.setenv("CORS_ORIGINS", " http://localhost:3000, ,http://localhost:3001 ")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    # pydantic bool parsing accepts yes/no — the shape operators actually type.
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "no")

    settings = Settings()

    assert settings.attestation.esign_required is False
    assert settings.app.app_env == "staging"
    assert settings.app.app_name == "risk-service-staging"
    assert (
        settings.database.database_url
        == "postgresql+psycopg://postgres:postgres@localhost:5432/risk"
    )
    assert settings.cors.origins == ["http://localhost:3000", "http://localhost:3001"]
    assert settings.logging.log_level == "DEBUG"


_BI_ENV_NAMES = (
    "BI_ENABLED",
    "BI_MART_ENQUEUE_ENABLED",
    "BI_SCHEDULER_ENABLED",
    "BI_DAILY_RETENTION_DAYS",
    "BI_DATABASE_URL",
    "BI_INTERACTIVE_TIMEOUT_MS",
    "BI_EXPORT_TIMEOUT_MS",
    "BI_UI_ROW_CAP",
    "BI_GRID_PAGE_CAP",
    "BI_EXPORT_ROW_CAP",
    "BI_EXPORT_ASYNC_THRESHOLD_ROWS",
    "BI_BACKFILL_HOP_SECONDS",
)


def test_bi_settings_ship_off_and_at_their_safe_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A deployment that sets nothing has no BI: no routers, no builds, no sweep."""
    monkeypatch.chdir(tmp_path)
    for name in _BI_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    bi = Settings().bi

    assert bi.enabled is False
    assert bi.mart_enqueue_enabled is False
    assert bi.scheduler_enabled is False
    assert bi.daily_retention_days == 95
    assert bi.database_url is None
    assert bi.interactive_timeout_ms == 10_000
    assert bi.export_timeout_ms == 120_000
    assert bi.ui_row_cap == 5_000
    assert bi.grid_page_cap == 500
    assert bi.export_row_cap == 100_000
    assert bi.export_async_threshold_rows == 10_000
    assert bi.backfill_hop_seconds == 600
    # Derived, never pinned: three hop budgets (hop + one overrunning date + margin).
    assert bi.backfill_stale_after_seconds == 1800.0
    # D-030: AG Grid Community — there is no licence key to configure.
    assert not any("license" in field for field in type(bi).model_fields)


def test_bi_settings_read_their_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_ENABLED", "yes")
    monkeypatch.setenv("BI_MART_ENQUEUE_ENABLED", "1")
    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("BI_DAILY_RETENTION_DAYS", "400")
    monkeypatch.setenv("BI_DATABASE_URL", "postgresql+psycopg://bi:bi@localhost:5432/risk")
    monkeypatch.setenv("BI_UI_ROW_CAP", "2500")
    monkeypatch.setenv("BI_BACKFILL_HOP_SECONDS", "300")

    bi = Settings().bi

    assert bi.enabled is True
    assert bi.mart_enqueue_enabled is True
    assert bi.scheduler_enabled is True
    assert bi.daily_retention_days == 400
    assert bi.database_url == "postgresql+psycopg://bi:bi@localhost:5432/risk"
    assert bi.ui_row_cap == 2500
    assert bi.backfill_hop_seconds == 300
    assert bi.backfill_stale_after_seconds == 900.0


def test_a_blank_bi_database_url_means_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same empty-neutralizes rule as DATABASE_URL, so conftest can blank it."""
    monkeypatch.setenv("BI_DATABASE_URL", "")
    assert Settings().bi.database_url is None
    monkeypatch.setenv("BI_DATABASE_URL", "   ")
    assert Settings().bi.database_url is None


def test_bi_limits_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_BACKFILL_HOP_SECONDS", "0")
    with pytest.raises(ValueError, match="BI_BACKFILL_HOP_SECONDS"):
        Settings()


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
