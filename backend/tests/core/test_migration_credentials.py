from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import get_migration_database_url, get_operator_settings, get_settings


@pytest.fixture(autouse=True)
def clear_operator_settings() -> Iterator[None]:
    get_operator_settings.cache_clear()
    yield
    get_operator_settings.cache_clear()


@pytest.mark.parametrize("environment", ["production", "staging"])
@pytest.mark.parametrize(
    "runtime_setting",
    [
        "DATABASE_URL",
        "WORKER_DATABASE_URL",
        "OPERATOR_DATABASE_URL",
        "BI_DATABASE_URL",
    ],
)
def test_migrations_refuse_runtime_role(
    environment: str, runtime_setting: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("DATABASE_URL", "postgresql://api:secret@database/risk")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://migrator:different@database/risk")
    monkeypatch.setenv(runtime_setting, "postgresql://migrator:runtime@database/risk")
    get_settings.cache_clear()
    get_operator_settings.cache_clear()
    with pytest.raises(RuntimeError, match="distinct from all runtime roles"):
        get_migration_database_url()


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_migrations_require_separate_credentials(
    environment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("DATABASE_URL", "postgresql://api:secret@database/risk")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="MIGRATION_DATABASE_URL"):
        get_migration_database_url()


def test_migrations_resolve_administrative_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://api:secret@database/risk")
    monkeypatch.setenv("WORKER_DATABASE_URL", "postgresql://worker:secret@database/risk")
    monkeypatch.setenv("OPERATOR_DATABASE_URL", "postgresql://operator:secret@database/risk")
    monkeypatch.setenv("BI_DATABASE_URL", "postgresql://bi:secret@database/risk")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://migrator:secret@database/risk")
    get_settings.cache_clear()
    get_operator_settings.cache_clear()
    assert get_migration_database_url() == "postgresql://migrator:secret@database/risk"


def test_local_migrations_keep_database_url_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://local:secret@database/risk")
    get_settings.cache_clear()
    assert get_migration_database_url() == "postgresql://local:secret@database/risk"
